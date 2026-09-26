"""Async agent-run API: submit a run, poll its status.

``POST /agent/async`` enqueues one agent request on the task queue and
returns a ``task_id`` immediately; ``GET /agent/status/{task_id}`` polls
the TaskTracker. Terminal webhooks (``agent.completed`` / ``agent.failed``)
are emitted by the job itself, so subscribers need not poll at all. Queue
infrastructure being down surfaces as 503, never a hang.

The status record is tenant-scoped: it stores the tenant that enqueued the
run, and a poll from any other tenant gets the same 404 as an unknown id.
The queue and tracker are synchronous Redis clients, so both calls run in a
worker thread (``asyncio.to_thread`` copies the request's context, tenant
included) instead of blocking the event loop.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.context import get_current_tenant_id
from core.middleware import require_user
from core.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/agent", dependencies=[Depends(require_user)])


class AsyncRunRequest(BaseModel):
    """Submission payload for an async agent run."""

    query: str = Field(..., min_length=1, max_length=8000)
    conversation_id: str | None = None


def _enqueue(query: str, conversation_id: str | None) -> str:
    from core.task_queue.jobs.agent_run import run_agent_task
    from core.task_queue.scheduler import enqueue_task

    return enqueue_task(run_agent_task, query, conversation_id=conversation_id)


def _tracker():
    from core.task_queue.status import get_task_tracker

    return get_task_tracker()


@router.post("/async", status_code=202)
async def submit_async_run(req: AsyncRunRequest) -> dict:
    """Enqueue an agent run; returns the task id to poll (or subscribe)."""
    try:
        task_id = await asyncio.to_thread(_enqueue, req.query, req.conversation_id)
    except Exception as exc:
        logger.warning("async_run_enqueue_failed error=%s", exc)
        raise HTTPException(status_code=503, detail="task queue unavailable") from exc
    return {"task_id": task_id, "status_url": f"/agent/status/{task_id}"}


@router.get("/status/{task_id}")
async def async_run_status(task_id: str) -> dict:
    """Current TaskTracker record for the run.

    404 when the id is unknown *or* belongs to another tenant — the two are
    deliberately indistinguishable.
    """
    tenant_id = get_current_tenant_id()

    def _read() -> dict | None:
        return _tracker().get_status_for_tenant(task_id, tenant_id)

    try:
        status = await asyncio.to_thread(_read)
    except Exception as exc:
        logger.warning("async_run_status_failed error=%s", exc)
        raise HTTPException(status_code=503, detail="task tracker unavailable") from exc
    if status is None:
        raise HTTPException(status_code=404, detail="unknown task id")
    return status


__all__ = ["router"]
