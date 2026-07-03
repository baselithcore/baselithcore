"""
Feedback Router.

Handles user feedback ingestion and aggregation for agent responses.
Used to measure generation quality and trigger self-improvement loops.
"""

import hashlib
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import ValidationError

from core.context import get_current_tenant_id
from core.middleware.security import require_admin, require_user
from core.models.chat import FeedbackDocumentReference, FeedbackRequest
from core.observability import telemetry
from core.observability.logging import get_logger
from core.observability.metrics import FEEDBACK_RECEIVED_TOTAL
from core.services.feedback_service import get_feedback_service

logger = get_logger(__name__)

router = APIRouter(prefix="", tags=["feedback"])


def _normalize_sources_payload(raw_sources: Any) -> list[dict[str, Any]] | None:
    """
    Format source references for storage and analytics.

    Args:
        raw_sources: Raw input sources payload.

    Returns:
        A list of normalized source dictionaries, or None if invalid.
    """
    if not raw_sources:
        return None

    normalized: list[dict[str, Any]] = []

    if isinstance(raw_sources, list):
        items = raw_sources
    else:
        items = [raw_sources]

    for source in items:
        if isinstance(source, FeedbackDocumentReference):
            normalized.append(source.model_dump(exclude_none=True))
            continue

        if isinstance(source, dict):
            cleaned = {
                str(key): value for key, value in source.items() if value is not None
            }
            if cleaned:
                normalized.append(cleaned)

    return normalized or None


@router.post("/feedback")
async def feedback(
    payload: dict[str, Any] = Body(...),
    _: str = Depends(require_user),
) -> dict[str, object]:
    """
    Records a feedback (positive|negative) for a generated response.
    - Data is saved to the PostgreSQL database (configurable via the dedicated environment variables).
    """
    try:
        req = FeedbackRequest(**payload)
        sources_payload = _normalize_sources_payload(req.sources)
        conversation_id = req.conversation_id
        query = req.query
        answer = req.answer
        feedback_value: Any = req.feedback
        comment = req.comment.strip() if isinstance(req.comment, str) else None
    except ValidationError as exc:
        # fallback for legacy/non-conforming payload. Apply the same length
        # caps as FeedbackRequest so this branch cannot be used to persist
        # unbounded attacker-supplied text (up to the request-size limit).
        query = str(payload.get("query") or "").strip()[:8000]
        answer = str(payload.get("answer") or "").strip()[:32000]
        feedback_value = str(payload.get("feedback") or "").strip().lower()
        if feedback_value not in {"positive", "negative"}:
            raise HTTPException(
                status_code=422,
                detail={"message": "Invalid feedback payload", "errors": exc.errors()},
            ) from exc
        raw_conversation_id = payload.get("conversation_id")
        conversation_id = (
            str(raw_conversation_id)[:128] if raw_conversation_id is not None else None
        )
        sources_payload = _normalize_sources_payload(payload.get("sources"))
        raw_comment = payload.get("comment")
        comment = raw_comment.strip()[:4000] if isinstance(raw_comment, str) else None

    feedback_service = get_feedback_service()
    await feedback_service.insert_feedback(
        query,
        answer,
        feedback_value,
        conversation_id=conversation_id,
        sources=sources_payload,
        comment=comment,
    )
    telemetry.increment(f"feedback.{feedback_value}")
    FEEDBACK_RECEIVED_TOTAL.labels(sentiment=feedback_value).inc()
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16] if query else ""
    logger.info(
        "feedback_recorded",
        extra={
            "event": "feedback_recorded",
            "tenant_id": get_current_tenant_id(),
            "feedback": feedback_value,
            "conversation_id": conversation_id,
            "query_hash": query_hash,
            "has_comment": bool(comment),
            "source_count": len(sources_payload) if sources_payload else 0,
        },
    )
    sanitized_payload = {
        "query": query,
        "answer": answer,
        "feedback": feedback_value,
        "conversation_id": conversation_id,
        "sources": sources_payload,
    }
    if comment:
        sanitized_payload["comment"] = comment
    return {"status": "ok", "received": sanitized_payload}


@router.get("/feedbacks")
async def list_feedbacks(
    _: str = Depends(require_admin),
    feedback: Literal["positive", "negative"] | None = Query(
        default=None,
        description="Filter results by feedback type: 'positive' or 'negative'",
    ),
    limit: int | None = Query(
        default=None,
        ge=1,
        le=200,
        description="Limit the number of returned records (max 200)",
    ),
) -> list[dict[str, object]]:
    """
    Returns all saved feedback entries.
    - If `feedback` is specified, filters by type ('positive' or 'negative').
    """
    feedback_service = get_feedback_service()
    return await feedback_service.get_feedbacks(feedback, limit=limit)
