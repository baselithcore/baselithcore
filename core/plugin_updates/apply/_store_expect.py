"""The run store's heartbeat and restart expectations (split for the size cap)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ._store_io import _atomic_write, _read
from .models import RELEASING_STATES, ApplyRun, Expectation, RunState, UpdaterHeartbeat


class ExpectationsMixin:
    """Heartbeat and per-plugin expectations of :class:`~.store.RunStore`."""

    _root: Path

    if TYPE_CHECKING:

        def _plugin(self, plugin: str) -> str: ...

        def get(self, run_id: str) -> ApplyRun | None: ...

    def write_heartbeat(self, hb: UpdaterHeartbeat) -> None:
        """Publish the updater's liveness."""
        _atomic_write(self._root / "heartbeat.json", hb.model_dump_json())

    def read_heartbeat(self) -> UpdaterHeartbeat | None:
        """The last heartbeat, or ``None`` when absent or unreadable."""
        try:
            text = _read(self._root / "heartbeat.json")
            return None if text is None else UpdaterHeartbeat.model_validate_json(text)
        except ValueError:
            return None

    def _expect_path(self, plugin: str) -> Path:
        return self._root / "expect" / f"{self._plugin(plugin)}.json"

    def write_expectation(self, expectation: Expectation) -> None:
        """Record what the API must report after the restart."""
        _atomic_write(
            self._expect_path(expectation.plugin), expectation.model_dump_json()
        )

    def read_expectation(self, plugin: str) -> Expectation | None:
        """The expectation for ``plugin``, or ``None``."""
        try:
            text = _read(self._expect_path(plugin))
            return None if text is None else Expectation.model_validate_json(text)
        except ValueError:
            return None

    def clear_expectation(self, plugin: str, run_id: str | None = None) -> None:
        """Forget the expectation for ``plugin`` (only ``run_id``'s, when given).

        A finishing run passes its id so it never deletes the expectation a
        later run of the same plugin already wrote.
        """
        path = self._expect_path(plugin)
        if run_id is not None:
            current = self.read_expectation(plugin)
            if current is None or current.run_id != run_id:
                return
        path.unlink(missing_ok=True)

    def clear_stale_expectations(self) -> list[str]:
        """Drop expectations whose run is unknown or finished; the plugins cleared.

        A run journals its terminal state before clearing its expectation, so a
        crash in between leaves one behind; reconciliation calls this.
        """
        done = RELEASING_STATES | {RunState.ROLLBACK_FAILED}
        cleared: list[str] = []
        for plugin in self.expected_plugins():
            expect = self.read_expectation(plugin)
            run = self.get(expect.run_id) if expect is not None else None
            if run is None or run.state in done:
                self.clear_expectation(plugin)
                cleared.append(plugin)
        return cleared

    def expected_plugins(self) -> list[str]:
        """Plugins with a pending expectation."""
        folder = self._root / "expect"
        return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


__all__ = ["ExpectationsMixin"]
