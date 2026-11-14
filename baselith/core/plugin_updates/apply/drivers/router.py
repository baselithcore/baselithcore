"""Route each run to the driver its subject and driver name select.

Selection: a run whose ``driver`` is not ``host`` belongs to a driver this
updater does not run (a GitOps run lives in the console's own table), so it is
failed with ``unknown_driver`` and never executed. A ``host`` run of the core
subject goes to ``core-host`` (``core_driver_unavailable`` when no such driver
is registered); every other ``host`` run goes to ``plugin-overlay``.

Recovery of a run whose driver cannot be resolved cannot know what the
interrupted run left behind, so it ends ``rollback_failed``: the claim stays
and an operator decides, exactly as for a rollback that failed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from ..models import CORE_SUBJECT, HOST_DRIVER, ApplyRun, RunState
from ..store import RunStateConflict, RunStore
from .base import CORE_HOST_DRIVER, PLUGIN_OVERLAY_DRIVER, ApplyDriver
from .registry import UnknownDriver, get_driver

logger = logging.getLogger(__name__)

UNKNOWN_DRIVER = "unknown_driver"
CORE_DRIVER_UNAVAILABLE = "core_driver_unavailable"


class _Unroutable(Exception):
    """The run's driver cannot be resolved; ``code`` is the failure to record."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DriverRouter:
    """An :class:`ApplyDriver` that delegates each run to the driver it names."""

    def __init__(
        self,
        store: RunStore,
        *,
        resolve: Callable[[str], ApplyDriver] = get_driver,
    ) -> None:
        self._store = store
        self._resolve = resolve

    @property
    def overlay_root(self) -> Path | None:
        """The plugin driver's overlay root (reconciliation cleans staging there)."""
        try:
            driver = self._resolve(PLUGIN_OVERLAY_DRIVER)
        except UnknownDriver:
            return None
        root = getattr(driver, "overlay_root", None)
        return root if isinstance(root, Path) else None

    def _driver_for(self, run: ApplyRun) -> ApplyDriver:
        if run.driver != HOST_DRIVER:
            raise _Unroutable(
                UNKNOWN_DRIVER, f"driver {run.driver!r} does not run in this updater"
            )
        if run.subject_key == CORE_SUBJECT:
            name, code = CORE_HOST_DRIVER, CORE_DRIVER_UNAVAILABLE
        else:
            name, code = PLUGIN_OVERLAY_DRIVER, UNKNOWN_DRIVER
        try:
            return self._resolve(name)
        except UnknownDriver:
            raise _Unroutable(code, f"no {name} driver in this updater") from None

    async def _settle(
        self, run: ApplyRun, state: RunState, exc: _Unroutable
    ) -> ApplyRun:
        logger.warning(
            "plugin_update_run_unroutable run=%s subject=%s driver=%s failure=%s",
            run.id,
            run.subject_key,
            run.driver,
            exc.code,
        )
        try:
            return await asyncio.to_thread(
                self._store.transition,
                run.id,
                state,
                message=exc.message,
                expect=run.state,
                failure=exc.code,
            )
        except RunStateConflict:  # someone else moved it: leave it alone
            return await asyncio.to_thread(self._store.get, run.id) or run

    async def execute(self, run: ApplyRun) -> ApplyRun:
        """Execute ``run`` with its driver, or fail it untouched when it has none."""
        try:
            driver = self._driver_for(run)
        except _Unroutable as exc:
            return await self._settle(run, RunState.FAILED, exc)
        return await driver.execute(run)

    async def resume_activation(self, run: ApplyRun) -> ApplyRun:
        """Resume activation with the run's driver (``rollback_failed`` without one)."""
        try:
            driver = self._driver_for(run)
        except _Unroutable as exc:
            return await self._settle(run, RunState.ROLLBACK_FAILED, exc)
        return await driver.resume_activation(run)

    async def redo_rollback(self, run: ApplyRun) -> ApplyRun:
        """Redo the rollback with the run's driver (``rollback_failed`` without one)."""
        try:
            driver = self._driver_for(run)
        except _Unroutable as exc:
            return await self._settle(run, RunState.ROLLBACK_FAILED, exc)
        return await driver.redo_rollback(run)

    def undo_swap(self, run: ApplyRun) -> None:
        """Undo the swap with the run's driver.

        Raises:
            ValueError: No driver resolves for the run; reconciliation then
                ends it ``rollback_failed``.
        """
        try:
            driver = self._driver_for(run)
        except _Unroutable as exc:
            raise ValueError(exc.message) from None
        driver.undo_swap(run)


__all__ = ["CORE_DRIVER_UNAVAILABLE", "UNKNOWN_DRIVER", "DriverRouter"]
