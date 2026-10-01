"""Switch, restart, verify and roll back: the second half of a run.

Split out of :mod:`.executor` for the file-size cap. Every blocking call (the
run store, the overlay link, pruning, the restart command) runs in a worker
thread; link switches and pruning hold the per-plugin run lock. Every terminal
state journals the state first, then clears the plugin's restart expectation,
and run messages never carry host paths.

Each restart follows one order: the expectation (with ``restart_at``) is
written, the live overlay link is switched, the restart command is issued. The
link is switched here, not earlier, so the running API keeps resolving the code
it booted with until the restart; a worker respawned between the switch and
the restart boots after ``restart_at`` on the new link, which is what the
verdict expects anyway. Switching is idempotent, so a resumed run simply
switches (again) and restarts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Collection
from datetime import datetime
from pathlib import Path
from typing import Any

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugins._overlay_guard import bundled_version, read_manifest_mapping
from core.plugins.overlay import STORE_DIRNAME
from core.plugins.overlay_prune import prune_stale_overlay

from ._io import CommandRunner
from .boot_report import latest_active_plugins
from .health import wait_healthy
from .models import ApplyRun, Expectation, RunKind, RunState
from .store import RunStore
from .swap import current_target, point_to, prune_store

logger = logging.getLogger(__name__)

LINK_REFUSED = "the plugin's overlay link could not be switched"
NO_BOOT_REPORT = (
    "no boot report yet — restart the API once with UPDATE_APPLY_ENABLED on"
)


class ActivationMixin:
    """Restart the API on a link, judge it, and put the previous link back on failure."""

    _store: RunStore
    _config: UpdateApplyConfig
    _overlay: Path
    _bundled: Path
    _runner: CommandRunner
    _probe: Callable[[], Awaitable[int]]
    _clock: Callable[[], float]
    _sleep: Callable[[float], Awaitable[None]]
    _now: Callable[[], datetime]

    # -- small helpers ------------------------------------------------------

    def _swap_locked(self, plugin: str, target: str | None, run_id: str) -> None:
        with self._store.plugin_lock(plugin):
            point_to(self._overlay, plugin, target, run_id=run_id)

    async def _swap(self, run: ApplyRun, target: str | None) -> bool:
        """Point the link at ``target``; False (logged, no path) when refused."""
        try:
            await asyncio.to_thread(self._swap_locked, run.plugin, target, run.id)
        except (OSError, ValueError) as exc:
            logger.error(
                "plugin_update_link_refused run=%s plugin=%s error=%s",
                run.id,
                run.plugin,
                type(exc).__name__,
            )
            return False
        return True

    async def _step(self, run: ApplyRun, state: RunState, **fields: Any) -> ApplyRun:
        return await asyncio.to_thread(self._store.transition, run.id, state, **fields)

    async def _finish(
        self, run: ApplyRun, state: RunState, message: str = "", **fields: Any
    ) -> ApplyRun:
        """Journal the terminal ``state``, then clear the restart expectation.

        A crash in between leaves an expectation of a finished run behind;
        reconciliation drops it (``RunStore.clear_stale_expectations``).
        """
        done = await self._step(run, state, message=message, **fields)
        try:
            await asyncio.to_thread(self._store.clear_expectation, run.plugin, run.id)
        except OSError as exc:
            logger.warning(
                "plugin_update_expectation_not_cleared plugin=%s error=%s",
                run.plugin,
                type(exc).__name__,
            )
        return done

    async def _survivors(self, plugin: str) -> list[str] | None:
        """Plugins active in the newest boot report, other than ``plugin``.

        Called once per run, before its first switch or restart, so the list
        describes the API as it ran before the run touched anything. ``None``
        when no boot report exists at all: nothing is known about what must
        survive, so the caller refuses the run.
        """
        active = await asyncio.to_thread(latest_active_plugins, self._store)
        return None if active is None else [p for p in active if p != plugin]

    async def _fail(self, run: ApplyRun, code: str, detail: str = "") -> ApplyRun:
        logger.warning(
            "AUDIT | PLUGIN_UPDATE | failed run=%s plugin=%s code=%s",
            run.id,
            run.plugin,
            code,
        )
        return await self._finish(run, RunState.FAILED, detail, failure=code)

    def _version_of(self, plugin: str, target: str | None) -> str | None:
        if target is None:
            return bundled_version(self._bundled, plugin)
        data = read_manifest_mapping(self._overlay / STORE_DIRNAME / target) or {}
        version = data.get("version")
        return str(version) if version else None

    def _prune_locked(self, plugin: str, keep: Collection[str]) -> None:
        with self._store.plugin_lock(plugin):
            prune_store(
                self._overlay,
                plugin,
                keep=keep,
                keep_versions=self._config.keep_versions,
            )
            prune_stale_overlay(self._overlay, self._bundled)

    def _rolled_back_note(self, run: ApplyRun, detail: str) -> str:
        """A rollback never reverts ``schema-init``: say so when it ran."""
        if run.kind is not RunKind.UPDATE or not self._config.schema_init:
            return detail
        note = f"schema changes from {run.to_version} remain"
        return f"{detail}; {note}" if detail else note

    # -- restart and verdict ------------------------------------------------

    async def _restart_and_check(
        self, run: ApplyRun, version: str | None, target: str | None
    ) -> tuple[bool, str, str]:
        """``(ok, failure code, detail)`` of switching to ``target`` and restarting.

        The expectation (with ``restart_at`` from the same wall clock the
        workers stamp ``booted_at`` with) is on disk before the link is
        switched and the restart issued, so no report of a worker that loaded
        ``target`` can predate it. ``overlay_refused`` when the switch failed.
        """
        if not self._config.restart_configured:
            return False, "restart_failed", "no restart command configured"
        expect = Expectation(
            run_id=run.id,
            plugin=run.plugin,
            version=version,
            store_dir=target,
            restart_at=self._now(),
            must_stay_active=list(run.must_stay_active or []),
        )
        await asyncio.to_thread(self._store.write_expectation, expect)
        if not await self._swap(run, target):
            return False, "overlay_refused", LINK_REFUSED
        rc = await asyncio.to_thread(
            self._runner,
            list(self._config.restart_command),
            timeout=float(self._config.restart_timeout_seconds),
        )
        if rc != 0:
            return False, "restart_failed", f"the restart command exited {rc}"
        if run.state is not RunState.ROLLING_BACK:  # a rollback stays ROLLING_BACK
            await self._step(run, RunState.HEALTH_CHECKING)
        verdict = await wait_healthy(
            self._store,
            expect,
            self._config,
            probe=self._probe,
            clock=self._clock,
            sleep=self._sleep,
        )
        return verdict.ok, "health_failed", verdict.reason

    async def _activate(
        self, run: ApplyRun, version: str | None, target: str | None
    ) -> ApplyRun:
        """Restart on ``target``; succeed (then prune) or roll back."""
        fields: dict[str, Any] = {}
        if run.must_stay_active is None:  # a run recorded before it was captured
            survivors = await self._survivors(run.plugin)
            if survivors is None:  # nothing known about dependents: never succeed blind
                return await self._roll_back(run, "health_failed", NO_BOOT_REPORT)
            fields["must_stay_active"] = survivors
        run = await self._step(run, RunState.ACTIVATING, **fields)
        ok, code, detail = await self._restart_and_check(run, version, target)
        if not ok and code == "overlay_refused" and await self._link_unmoved(run):
            # nothing switched or restarted; schema-init may have run already
            return await self._fail(run, code, self._rolled_back_note(run, detail))
        if not ok:
            current = await asyncio.to_thread(self._store.get, run.id)
            return await self._roll_back(current or run, code, detail)
        done = await self._finish(run, RunState.SUCCEEDED)
        keep = {t for t in (target, run.previous_target) if t}
        try:  # only after a successful switch AND a healthy verdict
            await asyncio.to_thread(self._prune_locked, run.plugin, keep)
        except OSError as exc:
            logger.warning(
                "plugin_update_prune_failed plugin=%s error=%s",
                run.plugin,
                type(exc).__name__,
            )
        logger.info(
            "AUDIT | PLUGIN_UPDATE | succeeded run=%s plugin=%s", run.id, run.plugin
        )
        return done

    async def _link_unmoved(self, run: ApplyRun) -> bool:
        """The live link still points at ``previous_target`` (unreadable: False)."""
        try:
            live = await asyncio.to_thread(current_target, self._overlay, run.plugin)
        except ValueError:
            return False
        return live == run.previous_target

    async def _roll_back(self, run: ApplyRun, code: str, detail: str) -> ApplyRun:
        """Point back at ``previous_target`` and restart; ROLLBACK_FAILED keeps the claim."""
        run = await self._step(run, RunState.ROLLING_BACK, failure=code, message=detail)
        version = await asyncio.to_thread(
            self._version_of, run.plugin, run.previous_target
        )
        ok, _, why = await self._restart_and_check(run, version, run.previous_target)
        if ok:
            return await self._finish(
                run,
                RunState.ROLLED_BACK,
                self._rolled_back_note(run, detail),
                failure=code,
            )
        logger.critical(
            "AUDIT | PLUGIN_UPDATE | rollback_failed run=%s plugin=%s: %s",
            run.id,
            run.plugin,
            why,
        )
        message = f"{detail}; rollback: {why}" if detail else f"rollback: {why}"
        return await self._finish(run, RunState.ROLLBACK_FAILED, message, failure=code)


__all__ = ["LINK_REFUSED", "NO_BOOT_REPORT", "ActivationMixin"]
