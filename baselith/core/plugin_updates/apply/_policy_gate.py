"""The maintenance window, checked again when the updater picks a policy run up.

:class:`~.policy_runner.PolicyRunner` checks the window and the estimate only
when it **creates** a run. The updater executes the oldest approved run first,
so a policy run created at the end of the window could wait behind a manual
run and start after the window closed: a restart of production the two
administrators never approved. :func:`admit_policy_run` closes that gap. A run
requested by ``policy:auto-patch`` starts only while the policy still covers
its subject, the window is open and its estimate fits in what is left;
otherwise it is **deferred**: moved to ``expired`` (it never executed, so the
next window tries the release again; ``expired`` does not count as a try) with
``failure`` ``window_closed`` or ``policy_off``, and
``update.policy_deferred`` is emitted with that code. Any other run is
admitted unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ..policy import UpdatePolicy
from ..window import in_window, remaining
from ._events import POLICY_DEFERRED_EVENT, emit_update_event
from .models import CORE_SUBJECT, PLUGIN_SUBJECT_PREFIX, ApplyRun, RunState
from .policy_runner import POLICY_ACTOR, journal_estimate
from .store import RunStateConflict, RunStore

logger = logging.getLogger(__name__)

WINDOW_CLOSED = "window_closed"
POLICY_OFF_CODE = "policy_off"


def _covered(policy: UpdatePolicy, subject: str) -> bool:
    if subject == CORE_SUBJECT:
        return policy.core == "patch"
    return policy.allows_plugin(subject.removeprefix(PLUGIN_SUBJECT_PREFIX))


def _refusal(
    store: RunStore, config: UpdateApplyConfig, run: ApplyRun, at: datetime
) -> str | None:
    policy = store.get_policy()
    window = policy.window
    if window is None or not _covered(policy, run.subject_key):
        return POLICY_OFF_CODE
    if not in_window(window, at):
        return WINDOW_CLOSED
    try:
        seconds = journal_estimate(
            store, run.subject_key, fallback=config.policy_estimate_seconds
        )
    except Exception as exc:  # an unreadable history: assume the default
        logger.info("update_policy_gate_estimate_failed error=%s", type(exc).__name__)
        seconds = config.policy_estimate_seconds
    if timedelta(seconds=seconds) > remaining(window, at):
        return WINDOW_CLOSED
    return None


async def admit_policy_run(
    store: RunStore, config: UpdateApplyConfig, run: ApplyRun, *, now: datetime
) -> bool:
    """Whether ``run`` may start now; a policy run that may not is deferred."""
    if run.requested_by != POLICY_ACTOR:
        return True
    try:
        code = await asyncio.to_thread(_refusal, store, config, run, now)
    except Exception as exc:  # an unknown verdict never starts a policy run
        logger.warning("update_policy_gate_failed error=%s", type(exc).__name__)
        return False
    if code is None:
        return True
    message = (
        "the maintenance window closed before it could start; the next window "
        "tries again"
        if code == WINDOW_CLOSED
        else "the policy no longer covers it; nothing was started"
    )
    try:
        await asyncio.to_thread(
            store.transition,
            run.id,
            RunState.EXPIRED,
            actor=POLICY_ACTOR,
            message=message,
            expect=RunState.APPROVED,
            failure=code,
        )
    except RunStateConflict:  # it moved meanwhile: not ours to defer
        return False
    logger.info(
        "update_policy_deferred code=%s subject=%s run=%s",
        code,
        run.subject_key,
        run.id,
    )
    await emit_update_event(
        POLICY_DEFERRED_EVENT, {"code": code, "subject": run.subject_key}
    )
    return False


__all__ = ["POLICY_OFF_CODE", "WINDOW_CLOSED", "admit_policy_run"]
