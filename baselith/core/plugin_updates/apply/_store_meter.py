"""The run store's metering marks: a finished run is counted once (split for the cap).

Several web workers sweep one store. The first to call :meth:`claim_metering`
for a finished run creates ``metered/<id>`` with ``O_EXCL`` and counts it;
every later call, in any process and after any restart, answers False. A
worker that dies between the claim and the increment loses that one count:
the counter may undercount by a run, never double count.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ._store_io import _RUN_ID


class MeterMixin:
    """Metering marks of :class:`~.store.RunStore`."""

    _root: Path

    if TYPE_CHECKING:

        def _run_path(self, run_id: str) -> Path: ...

    def _meter_mark(self, run_id: str) -> Path:
        self._run_path(run_id)  # validates the id
        return self._root / "metered" / run_id

    def claim_metering(self, run_id: str) -> bool:
        """True exactly once per run, across processes: the caller counts it."""
        path = self._meter_mark(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            return False
        return True

    def release_metering(self, run_id: str) -> None:
        """Forget a claim (the count could not be taken), so a later sweep retries."""
        self._meter_mark(run_id).unlink(missing_ok=True)

    def is_metered(self, run_id: str) -> bool:
        """Whether the run was already counted."""
        return _RUN_ID.match(run_id) is not None and (
            (self._root / "metered" / run_id).exists()
        )


__all__ = ["MeterMixin"]
