"""Async agent-run job.

Closes the async-agents gap: the queue machinery (RQ workers, TaskTracker,
dead-letter, webhooks) existed but no job could run an *agent request* —
only documents/indexing were enqueueable. This job executes one chat/agent
request on a worker, tracks its lifecycle in the TaskTracker (poll via
``GET /agent/status/{task_id}``), and emits a terminal webhook
(``agent.completed`` / ``agent.failed``) so callers can subscribe instead
of polling. Webhook delivery is best-effort — a webhook outage never fails
a finished run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rq import get_current_job

from core.observability.logging import get_logger

logger = get_logger(__name__)

AGENT_COMPLETED_EVENT = "agent.completed"
AGENT_FAILED_EVENT = "agent.failed"


def _get_tracker() -> Any:
    from core.task_queue.status import get_task_tracker

    return get_task_tracker()


def _get_webhooks() -> Any:
    from core.webhooks.service import get_webhook_service

    return get_webhook_service()


async def _handle_chat(req: Any) -> Any:
    from core.chat import chat_service

    return await chat_service.handle_chat_async(req)


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
        Exception: Whatever the agent raised — RQ records the job failed
            (and the dead-letter machinery applies); the failure webhook is
            emitted first.
    """
    job = get_current_job()
    job_id = job.id if job else "unknown"
    tracker = _get_tracker()
    tracker.mark_started(job_id, f"Agent run: {query[:80]}")

    async def _run() -> dict[str, Any]:
        from core.models.chat import ChatRequest

        req = ChatRequest(query=query, conversation_id=conversation_id)
        response = await _handle_chat(req)
        return {
            "answer": response.answer,
            "metadata": dict(response.metadata or {}),
        }

    async def _notify(event: str, data: dict[str, Any]) -> None:
        try:
            await _get_webhooks().emit(event, data)
        except Exception as exc:
            logger.warning("agent_run_webhook_failed event=%s error=%s", event, exc)

    async def _run_and_notify() -> dict[str, Any]:
        try:
            payload = await _run()
        except Exception as exc:
            await _notify(AGENT_FAILED_EVENT, {"task_id": job_id, "error": str(exc)})
            raise
        await _notify(AGENT_COMPLETED_EVENT, {"task_id": job_id, **payload})
        return payload

    try:
        result = asyncio.run(_run_and_notify())
    except Exception as exc:
        tracker.mark_failed(job_id, str(exc))
        raise
    tracker.mark_completed(job_id, "Agent run completed", result=result)
    return result


__all__ = ["AGENT_COMPLETED_EVENT", "AGENT_FAILED_EVENT", "run_agent_task"]
