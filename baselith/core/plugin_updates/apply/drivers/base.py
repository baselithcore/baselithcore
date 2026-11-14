"""The interface every apply driver implements.

A driver takes an approved run to a terminal state (``execute``) and settles a
run a previous updater process left half done (the three recovery entry
points :func:`~..reconcile.reconcile` drives). The plugin overlay
:class:`~..executor.Executor` satisfies it structurally, unedited; the core
host driver is a second implementation.
"""

from __future__ import annotations

from typing import Final, Protocol

from ..models import ApplyRun

#: Registry name of the driver that installs plugin releases into the overlay.
PLUGIN_OVERLAY_DRIVER: Final = "plugin-overlay"
#: Registry name of the driver that installs the core in place on a host.
CORE_HOST_DRIVER: Final = "core-host"


class ApplyDriver(Protocol):
    """What the updater drives for one run."""

    async def execute(self, run: ApplyRun) -> ApplyRun:
        """Take an approved run to a terminal state (or ``rollback_failed``)."""
        ...

    async def resume_activation(self, run: ApplyRun) -> ApplyRun:
        """Reconcile a run interrupted while activating or health checking."""
        ...

    async def redo_rollback(self, run: ApplyRun) -> ApplyRun:
        """Reconcile a run interrupted while rolling back (idempotent)."""
        ...

    def undo_swap(self, run: ApplyRun) -> None:
        """Reconcile a run interrupted while migrating: restore what it switched.

        Raises:
            OSError | ValueError: The previous state could not be restored
                (reconciliation then ends the run ``rollback_failed``).
        """
        ...


__all__ = ["CORE_HOST_DRIVER", "PLUGIN_OVERLAY_DRIVER", "ApplyDriver"]
