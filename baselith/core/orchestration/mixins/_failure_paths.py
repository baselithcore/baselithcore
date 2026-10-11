"""What the execution mixin does when a run does not finish normally.

Three ways out of the handler, three different obligations:

* **Budget exceeded** — the caps did their job. The checkpoint is failed with
  the offending cap named, and the caller gets a structured refusal carrying
  the budget snapshot.
* **Cancelled** — the client disconnected, the request timed out, or the
  process is shutting down. ``asyncio.CancelledError`` is *not* an
  ``Exception``, so the generic handler below never saw it and the checkpoint
  stayed ``running`` until the stale sweep noticed, half an hour later. Record
  it, then **re-raise**: swallowing a cancellation breaks structured
  concurrency (the awaiting task never learns it was cancelled). A
  cancellation while the process is **draining** (SIGTERM, a rolling deploy)
  is not the run's end: the run is saved ``running`` with a
  :data:`~baselith.core.orchestration.checkpoint_lease.YIELDED_KEY` marker
  and its lease released, so another replica's recovery sweep resumes it at
  once. Any other cancellation fails the run with ``cancelled``.
* **Checkpoint conflict** — another executor wrote the run (it claimed the
  lease after this attempt lost it, or the stale sweep failed it). This copy
  is stale: nothing is written — a ``fail()`` would clobber the newer state —
  and the caller gets an error result.
* **Anything else** — mark the checkpoint failed but keep it, so the run stays
  resumable and its completed tool steps replay instead of re-executing.

Split from :mod:`baselith.core.orchestration.mixins.execution` for the module size cap.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.checkpoint_errors import CheckpointConflictError
from baselith.core.orchestration.checkpoint_lease import YIELDED_KEY
from baselith.core.orchestration.limits import BudgetExceededError
from baselith.core.orchestration.run_events import EventType, publish_run_event

try:
    from baselith.core.events import EventNames, get_event_bus

    _HAS_EVENT_BUS = True
except ImportError:  # pragma: no cover - optional event bus
    _HAS_EVENT_BUS = False

logger = get_logger(__name__)

__all__ = [
    "handle_budget_exceeded",
    "handle_cancellation",
    "handle_checkpoint_conflict",
    "handle_handler_error",
]


async def handle_budget_exceeded(
    exc: BudgetExceededError,
    intent: str | None,
    checkpoint_mgr: Any | None,
    events_run_id: str | None,
) -> dict[str, Any]:
    """Fail the checkpoint and return the structured budget refusal."""
    logger.warning(
        "loop_budget_exceeded",
        extra={"intent": intent, "reason": exc.reason, "snapshot": str(exc.snapshot)},
    )
    if checkpoint_mgr is not None:
        try:
            await checkpoint_mgr.fail(f"budget_exceeded: {exc.reason}")
        except CheckpointConflictError as conflict:
            # Someone else owns the run now; their state is the one to keep.
            logger.warning("budget_exceeded_not_recorded error=%s", conflict)
    publish_run_event(
        events_run_id,
        EventType.ERROR,
        {"error": f"budget_exceeded: {exc.reason}"},
    )
    return {
        "response": f"Request aborted: {exc.reason}",
        "intent": intent,
        "error": True,
        "budget_exceeded": exc.reason,
        "budget": exc.snapshot.__dict__,
    }


async def handle_cancellation(
    intent: str | None,
    checkpoint_mgr: Any | None,
    events_run_id: str | None,
) -> None:
    """Record a cancelled run on its checkpoint. The caller must re-raise.

    The write is **shielded**. Cancellation arrives in waves — a shutdown
    cancels the task, and the task's own cleanup is a prime target for the
    next one — so an unshielded ``await`` here would itself be cancelled and
    the run would stay ``running`` forever, which is precisely the state this
    function exists to prevent.

    Every failure is swallowed (``BaseException``, because a second
    ``CancelledError`` is the expected one): losing the record must not
    replace the original cancellation with an unrelated error, and the caller
    re-raises either way.
    """
    from baselith.core.lifecycle.drain import is_draining

    draining = is_draining()
    logger.info(
        "run_cancelled",
        extra={"intent": intent, "run_id": events_run_id, "draining": draining},
    )
    if checkpoint_mgr is not None:
        try:
            if draining:
                await asyncio.shield(_yield_for_shutdown(checkpoint_mgr))
            else:
                await asyncio.shield(checkpoint_mgr.fail("cancelled"))
        except BaseException as exc:
            logger.warning("Failed to persist checkpoint cancellation: %s", exc)
    error = "interrupted_by_shutdown" if draining else "cancelled"
    publish_run_event(events_run_id, EventType.ERROR, {"error": error})


async def _yield_for_shutdown(checkpoint_mgr: Any) -> None:
    """Leave the run resumable: ``running``, marked yielded, budget saved.

    The lease is released by ``process()``'s ``finally`` right after, so the
    next recovery sweep on any replica re-enters the run immediately.
    """
    from baselith.core.orchestration.checkpoint import STATUS_RUNNING

    checkpoint = checkpoint_mgr.checkpoint
    checkpoint.status = STATUS_RUNNING
    checkpoint.plugin_data[YIELDED_KEY] = {"reason": "shutdown", "at": time.time()}
    sync = getattr(checkpoint_mgr, "_sync_budget", None)
    if sync is not None:
        sync()
    persist = getattr(checkpoint_mgr, "_persist", None)
    if persist is not None:
        await persist()
    else:
        await checkpoint_mgr.store.save(checkpoint)


def handle_checkpoint_conflict(
    exc: Exception, intent: str | None, events_run_id: str | None
) -> dict[str, Any]:
    """Stop a run whose checkpoint moved on underneath it; write nothing."""
    logger.warning(
        "checkpoint_conflict_run_stopped",
        extra={"intent": intent, "run_id": events_run_id, "error": str(exc)},
    )
    publish_run_event(events_run_id, EventType.ERROR, {"error": "checkpoint_conflict"})
    return {
        "response": "Request stopped: this run is being executed elsewhere.",
        "intent": intent,
        "error": True,
        "checkpoint_conflict": True,
    }


async def handle_handler_error(
    exc: Exception,
    intent: str | None,
    checkpoint_mgr: Any | None,
    events_run_id: str | None,
    start_time: float,
) -> dict[str, Any]:
    """Fail the checkpoint, emit the failure event, return the error result."""
    logger.error(f"Handler error for intent {intent}: {exc}")
    # Mark failed but keep the checkpoint — a resumable run survives the crash
    # and completed steps replay instead of re-executing.
    if checkpoint_mgr is not None:
        try:
            await checkpoint_mgr.fail(str(exc))
        except Exception as cp_err:
            logger.warning(f"Failed to persist checkpoint failure: {cp_err}")

    if _HAS_EVENT_BUS:
        elapsed = time.time() - start_time
        try:
            get_event_bus().emit_sync(
                EventNames.FLOW_COMPLETED,
                {
                    "intent": intent,
                    "duration_ms": int(elapsed * 1000),
                    "success": False,
                    "error": str(exc),
                    "run_id": events_run_id,
                },
            )
        except Exception as e_emit:
            logger.warning(f"Failed to emit failure event: {e_emit}")

    publish_run_event(events_run_id, EventType.ERROR, {"error": str(exc)})
    return {
        "response": f"Error processing request: {exc!s}",
        "intent": intent,
        "error": True,
    }
