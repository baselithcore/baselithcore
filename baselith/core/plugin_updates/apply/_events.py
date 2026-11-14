"""The updater's events: a rollback that failed, and the policy runner's verdicts.

``plugin.update_rollback_failed`` (below) and ``update.policy_applied`` /
``update.policy_deferred`` (the auto-apply policy created a run, or put one
off: ``queue_busy``, ``probe_failed``, ``window_too_short``) are emitted by the
updater process.

The event goes out on the updater's own in-process bus (the one
:mod:`baselith.core.plugin_updates.service` uses for ``plugin.update_available``). That
bus is not shared with the API workers, so the web tier re-announces the
outcome when it records it (once per run, ``RunStore.mark_audited``); the
CRITICAL ``AUDIT | PLUGIN_UPDATE | rollback_failed`` log line the executor
writes is the durable signal. The payload holds names, versions and codes,
never a path, URL or credential.
"""

from __future__ import annotations

import logging

from baselith.core.events import get_event_bus

from .models import ApplyRun, RunState

logger = logging.getLogger(__name__)

ROLLBACK_FAILED_EVENT = "plugin.update_rollback_failed"
#: The policy created a pre-approved run: ``{subject, from_version, to_version, run_id}``.
POLICY_APPLIED_EVENT = "update.policy_applied"
#: The policy put a run off: ``{code, subject}``.
POLICY_DEFERRED_EVENT = "update.policy_deferred"


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


async def emit_update_event(name: str, payload: dict[str, object]) -> bool:
    """Emit ``name`` on the bus; True when emitted. Never raises.

    The payload carries names, versions and codes only. A bus failure is
    logged with the exception's type name and nothing else.
    """
    try:
        await get_event_bus().emit(name, payload, source="plugin_updates")
    except Exception as exc:
        logger.warning(
            "plugin_update_event_failed event=%s error=%s", name, type(exc).__name__
        )
        return False
    return True


__all__ = [
    "POLICY_APPLIED_EVENT",
    "POLICY_DEFERRED_EVENT",
    "ROLLBACK_FAILED_EVENT",
    "announce_rollback_failed",
    "emit_update_event",
    "rollback_failed_payload",
]
