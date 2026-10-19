"""Online evaluation read API.

``GET /evaluation/online/summary`` reports the online evaluator's sampling
settings, queue depth and, per intent and source (``checks`` / ``judge``), the
rolling-window mean, the reference mean and whether the intent is currently
degraded. Mounted when ``EVAL_ONLINE_ENABLED`` is on; answers 503 while the
service is not running (``EVAL_ENABLED`` off, or a failed start).

``scope`` says whose traffic the window statistics cover. ``fleet`` when the
fleet monitor is on (``CACHE_BACKEND=redis`` and ``EVAL_ONLINE_FLEET``) and
Redis answered: every worker's sampled traffic, read from the shared windows.
``worker`` otherwise: only the share the serving worker sampled. The sampling
settings and ``queue_depth`` are always the serving worker's.

Protected by the same admin Basic Auth as the admin router.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from baselith.plugins.api_routers.routes.admin import verify_credentials
from baselith.plugins.api_routers.schemas import problem_responses

router = APIRouter(
    prefix="/evaluation",
    tags=["evaluation"],
    dependencies=[Depends(verify_credentials)],
)


class OnlineIntentWindow(BaseModel):
    """Rolling-window statistics of one intent and score source."""

    intent: str
    source: str
    window_mean: float
    window_samples: int
    reference_mean: float | None = None
    observed: int
    degraded: bool


class OnlineEvaluationSummary(BaseModel):
    """The online evaluator's state, fleet-wide or on the serving worker."""

    running: bool
    scope: Literal["fleet", "worker"]
    sample_rate: float
    judge_rate: float
    queue_depth: int
    intents: list[OnlineIntentWindow]


@router.get(
    "/online/summary",
    response_model=OnlineEvaluationSummary,
    responses=problem_responses(401, 503),
)
async def online_summary(request: Request) -> dict[str, Any]:
    """Per-intent rolling-window quality of the sampled production traffic."""
    service = getattr(request.app.state, "evaluation_service", None)
    if service is None or not getattr(service, "running", False):
        raise HTTPException(
            status_code=503,
            detail="Online evaluation is not running "
            "(set EVAL_ENABLED=true and EVAL_ONLINE_ENABLED=true).",
        )
    summary: dict[str, Any] = await service.online_summary()
    return summary
