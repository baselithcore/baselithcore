"""
Run state-history and time-travel API.

Operator surface over versioned checkpoint snapshots
(:mod:`core.orchestration.checkpoint_history`): inspect a run's state history,
fetch the full state at a version, and fork a run from any recorded version
into a fresh resumable run (rewind = fork at an earlier version, then resume
via the orchestrator or ``POST /approvals/{run_id}/resume``).

Mounted only when ``ORCHESTRATOR_CHECKPOINT_ENABLED`` is set (see
``ApiRoutersPlugin.get_routers``); snapshots are only recorded when
``ORCHESTRATOR_CHECKPOINT_HISTORY_ENABLED`` is also set. Protected by the same
admin Basic Auth as the admin router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.observability.logging import get_logger
from core.orchestration.checkpoint_factory import get_default_checkpoint_store
from core.orchestration.checkpoint_history import (
    fork_run,
    get_state,
    get_state_history,
)
from core.orchestration.run_events import (
    TERMINAL_EVENT_TYPES,
    get_run_event_stream,
)
from plugins.api_routers.admin import verify_credentials

logger = get_logger(__name__)

router = APIRouter(
    prefix="/runs",
    tags=["runs"],
    dependencies=[Depends(verify_credentials)],
)


class ForkRequest(BaseModel):
    """Fork a run from the state recorded at ``version``."""

    version: int = Field(ge=1)
    new_run_id: str | None = Field(default=None, max_length=200)


def _require_store() -> Any:
    store = get_default_checkpoint_store()
    if store is None:
        raise HTTPException(
            status_code=503,
            detail="Checkpointing is disabled "
            "(set ORCHESTRATOR_CHECKPOINT_ENABLED=true).",
        )
    return store


@router.get("/{run_id}/history")
async def state_history(run_id: str) -> dict[str, Any]:
    """Version-ascending snapshot summaries for a run.

    Empty history for a known run means snapshots are not being recorded
    (``ORCHESTRATOR_CHECKPOINT_HISTORY_ENABLED`` is off).
    """
    store = _require_store()
    history = await get_state_history(store, run_id)
    if not history and await store.load(run_id) is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return {"run_id": run_id, "history": history, "count": len(history)}


@router.get("/{run_id}/history/{version}")
async def state_at_version(run_id: str, version: int) -> dict[str, Any]:
    """The full checkpoint state exactly as recorded at ``version``."""
    store = _require_store()
    state = await get_state(store, run_id, version)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No snapshot for run '{run_id}' at version {version}.",
        )
    return state.to_dict()


@router.get("/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    """Stream a run's structured events as Server-Sent Events.

    Frames are ``event: <type>`` + ``data: <AgentEvent JSON>``; the stream
    closes after a terminal event (final answer, error, or durable approval
    pause). Subscribe **before** starting/resuming the run — events are
    fan-out only, not replayed (the checkpoint trajectory is the durable
    record).
    """
    stream = get_run_event_stream()

    async def event_frames():
        async with stream.subscribe(run_id) as subscription:
            async for event in subscription:
                payload = event.model_dump_json()
                yield f"event: {event.type.value}\ndata: {payload}\n\n"
                if event.type in TERMINAL_EVENT_TYPES:
                    break

    return StreamingResponse(event_frames(), media_type="text/event-stream")


@router.post("/{run_id}/fork")
async def fork(run_id: str, request: ForkRequest) -> dict[str, Any]:
    """Fork a run from its state at a version into a fresh resumable run.

    The fork keeps the recorded steps up to that version, so resuming it
    replays them without re-executing side effects and continues live from
    the fork point.
    """
    store = _require_store()
    forked = await fork_run(store, run_id, request.version, request.new_run_id)
    if forked is None:
        raise HTTPException(
            status_code=404,
            detail=f"No snapshot for run '{run_id}' at version {request.version}.",
        )
    logger.info(
        "run_forked source=%s version=%d fork=%s",
        run_id,
        request.version,
        forked.run_id,
    )
    return {
        "source_run_id": run_id,
        "source_version": request.version,
        "run_id": forked.run_id,
        "status": forked.status,
        "steps": len(forked.steps),
    }
