"""Async agent-run job.

Closes the async-agents gap: the queue machinery (RQ workers, TaskTracker,
dead-letter, webhooks) existed but no job could run an *agent request* —
only documents/indexing were enqueueable. This job executes one chat/agent
request on a worker, tracks its lifecycle in the TaskTracker (poll via
``GET /agent/status/{task_id}``), and emits a terminal webhook
(``agent.completed`` / ``agent.failed``) so callers can subscribe instead
of polling. Webhook delivery is best-effort — a webhook outage never fails
a finished run.

Failure is terminal only on the last attempt. The scheduler enqueues with
RQ ``Retry`` (``TASK_QUEUE_DEFAULT_RETRY_COUNT``), so an attempt that raises
while the job still has ``retries_left`` is recorded as the non-terminal
``retrying`` status and emits no webhook; only the attempt that exhausts the
budget (or a job enqueued without retries) marks ``failed`` and emits
``agent.failed``. Otherwise a poller would see ``failed`` and stop while a
later retry completes the run.

Every attempt of a job is the same durable run: the run id is derived from the
RQ job id (:func:`agent_job_run_id`), so a retry resumes the run's checkpoint
— completed tool and LLM steps replay — instead of starting over, and the
idempotency ledger matches the effects the failed attempt already caused. A
retry of a run that already completed returns its recorded answer.

If the worker dies mid-run, the API's recovery sweep may finish the run
before (or instead of) an RQ retry. The run carries a completion notifier
(``completion_notify`` in its inputs), and the sweep calls
:func:`notify_resumed_run`: the TaskTracker and the terminal webhook are
updated exactly as a normal completion would. Delivery is at-least-once — a
later RQ retry of the same job returns the recorded answer and notifies again.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rq import get_current_job

from baselith.core.observability.logging import get_logger
from baselith.core.orchestration.run_completion import COMPLETION_NOTIFY_KEY

logger = get_logger(__name__)

AGENT_COMPLETED_EVENT = "agent.completed"
AGENT_FAILED_EVENT = "agent.failed"


def _get_tracker() -> Any:
    from baselith.core.task_queue.status import get_task_tracker

    return get_task_tracker()


def _get_webhooks() -> Any:
    from baselith.core.webhooks.service import get_webhook_service

    return get_webhook_service()


async def _handle_chat(
    req: Any, run_id: str | None = None, extra_context: dict[str, Any] | None = None
) -> Any:
    from baselith.core.chat import chat_service

    return await chat_service.handle_chat_async(
        req, run_id=run_id, extra_context=extra_context
    )


def agent_job_run_id(job_id: str) -> str:
    """The durable run id shared by every attempt of the RQ job ``job_id``."""
    from baselith.core.orchestration.run_ids import derive_run_id

    return derive_run_id("agent-job", job_id)


async def notify_resumed_run(spec: dict[str, Any], result: dict[str, Any]) -> None:
    """Report a run the recovery sweep finished on behalf of a dead job.

    Registered as the ``task_queue.agent_run`` completion notifier (see
    :mod:`baselith.core.orchestration.run_completion`).
    """
    task_id = str(spec.get("task_id", ""))
    if not task_id or result.get("awaiting_approval"):
        return  # not terminal: the approval resume finishes it later
    tracker = _get_tracker()
    if result.get("error"):
        error = str(result.get("response", "agent run failed"))
        await asyncio.to_thread(tracker.mark_failed, task_id, error)
        await _emit(AGENT_FAILED_EVENT, {"task_id": task_id, "error": error})
        return
    payload = {
        "answer": result.get("response", ""),
        "metadata": dict(result.get("metadata") or {}),
    }
    await asyncio.to_thread(
        tracker.mark_completed,
        task_id,
        "Agent run completed (resumed by recovery)",
        result=payload,
    )
    await _emit(AGENT_COMPLETED_EVENT, {"task_id": task_id, **payload})


async def _emit(event: str, data: dict[str, Any]) -> None:
    """Best-effort webhook: a webhook outage never fails a finished run."""
    try:
        await _get_webhooks().emit(event, data)
    except Exception as exc:
        logger.warning("agent_run_webhook_failed event=%s error=%s", event, exc)


def _retries_left(job: Any) -> int:
    """Retries RQ still holds for *job* after the running attempt.

    RQ decrements ``retries_left`` only after an attempt fails, so inside the
    job it counts the attempts still to come; ``None`` means no retry policy.
    """
    left = getattr(job, "retries_left", None) if job is not None else None
    return left if isinstance(left, int) and left > 0 else 0


def run_agent_task(
    query: str,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    """Execute one agent request on a worker; return the answer payload.

    Args:
        query: The user query for the agent.
        conversation_id: Optional conversation to continue.

    Returns:
        ``{"answer": str, "metadata": dict}`` — also stored as the task
        result in the TaskTracker.

    Raises:
        Exception: Whatever the agent raised — RQ retries the job or, on the
            last attempt, records it failed (and the dead-letter machinery
            applies); the failure webhook is emitted first on that last
            attempt only.
    """
    job = get_current_job()
    job_id = job.id if job else "unknown"
    retries_left = _retries_left(job)
    tracker = _get_tracker()
    tracker.mark_started(job_id, f"Agent run: {query[:80]}")

    async def _run() -> dict[str, Any]:
        from baselith.core.models.chat import ChatRequest

        req = ChatRequest(query=query, conversation_id=conversation_id)
        response = await _handle_chat(
            req,
            run_id=agent_job_run_id(job_id),
            extra_context={
                COMPLETION_NOTIFY_KEY: {
                    "kind": "task_queue.agent_run",
                    "task_id": job_id,
                }
            },
        )
        return {
            "answer": response.answer,
            "metadata": dict(response.metadata or {}),
        }

    _notify = _emit

    async def _run_and_notify() -> dict[str, Any]:
        try:
            payload = await _run()
        except Exception as exc:
            if retries_left == 0:
                await _notify(
                    AGENT_FAILED_EVENT, {"task_id": job_id, "error": str(exc)}
                )
            raise
        await _notify(AGENT_COMPLETED_EVENT, {"task_id": job_id, **payload})
        return payload

    try:
        result = asyncio.run(_run_and_notify())
    except Exception as exc:
        if retries_left:
            tracker.mark_retrying(job_id, str(exc), retries_left)
        else:
            tracker.mark_failed(job_id, str(exc))
        raise
    tracker.mark_completed(job_id, "Agent run completed", result=result)
    return result


__all__ = [
    "AGENT_COMPLETED_EVENT",
    "AGENT_FAILED_EVENT",
    "agent_job_run_id",
    "notify_resumed_run",
    "run_agent_task",
]
