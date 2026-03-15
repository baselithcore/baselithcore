"""
Relational Knowledge Linking Helpers.

Implements domain-agnostic patterns for connecting disparate knowledge
entities. Supports cross-referencing between user stories, documentation
artifacts, and external issue trackers to build a cohesive trace
of system intent and status.
"""

from __future__ import annotations

from core.observability import get_logger
from typing import Optional, Callable
from datetime import datetime, timezone

logger = get_logger(__name__)


def record_document_feedback(
    query_fn: Callable,
    document_id: str,
    feedback: str,
    comment: Optional[str] = None,
) -> None:
    """
    Update feedback counters on a Document node.

    Tracks:
    - feedback_total
    - feedback_positive / feedback_negative
    - last_feedback_at (ISO8601)
    - last_feedback_comment (truncated to 500 chars)

    Args:
        query_fn: Query execution function
        document_id: Document identifier
        feedback: Feedback sentiment ("positive" or "negative")
        comment: Optional feedback comment
    """
    if not document_id:
        return

    sentiment = feedback.strip().lower()
    is_positive = sentiment == "positive"
    is_negative = sentiment == "negative"
    now_iso = datetime.now(timezone.utc).isoformat()
    trimmed_comment = (comment or "").strip()
    if trimmed_comment:
        trimmed_comment = trimmed_comment[:500]  # security/readability limit

    cypher = (
        "MERGE (d:Document {id: $id, tenant_id: $tenant_id}) "
        "SET d.feedback_total = coalesce(d.feedback_total, 0) + 1, "
        "    d.last_feedback_at = $now "
        + (
            "    , d.feedback_positive = coalesce(d.feedback_positive, 0) + 1 "
            if is_positive
            else ""
        )
        + (
            "    , d.feedback_negative = coalesce(d.feedback_negative, 0) + 1 "
            if is_negative
            else ""
        )
        + ("    , d.last_feedback_comment = $comment " if trimmed_comment else "")
        + "RETURN d.id"
    )
    query_fn(
        cypher,
        {"id": document_id, "now": now_iso, "comment": trimmed_comment},
    )
