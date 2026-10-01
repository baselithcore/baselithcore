"""The ``plugin.update_rollback_failed`` event, emitted by the updater process.

The event goes out on the updater's own in-process bus (the one
:mod:`core.plugin_updates.service` uses for ``plugin.update_available``). That
bus is not shared with the API workers, so the web tier re-announces the
outcome when it records it (once per run, ``RunStore.mark_audited``); the
CRITICAL ``AUDIT | PLUGIN_UPDATE | rollback_failed`` log line the executor
writes is the durable signal. The payload holds names, versions and codes,
never a path, URL or credential.
"""

from __future__ import annotations

import logging

from core.events import get_event_bus

from .models import ApplyRun, RunState

logger = logging.getLogger(__name__)

ROLLBACK_FAILED_EVENT = "plugin.update_rollback_failed"


def rollback_failed_payload(run: ApplyRun) -> dict[str, object]:
    """What the event carries about ``run``."""
    return {
        "run_id": run.id,
        "plugin": run.plugin,
        "kind": run.kind.value,
        "from_version": run.from_version,
        "to_version": run.to_version,
        "failure": run.failure,
    }


async def announce_rollback_failed(run: ApplyRun | None) -> bool:
    """Emit the event when ``run`` ended ``rollback_failed``; True when emitted.

    Never raises: a bus failure is logged (type name only) and the run's
    state, which keeps the plugin's claim, is the signal that remains.
    """
    if run is None or run.state is not RunState.ROLLBACK_FAILED:
        return False
    try:
        await get_event_bus().emit(
            ROLLBACK_FAILED_EVENT,
            rollback_failed_payload(run),
            source="plugin_updates",
        )
    except Exception as exc:
        logger.warning(
            "plugin_update_event_failed event=%s error=%s",
            ROLLBACK_FAILED_EVENT,
            type(exc).__name__,
        )
        return False
    return True


__all__ = [
    "ROLLBACK_FAILED_EVENT",
    "announce_rollback_failed",
    "rollback_failed_payload",
]
