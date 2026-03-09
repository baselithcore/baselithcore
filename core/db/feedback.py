"""
Feedback Database Access.

Provides functions for inserting and retrieving chat feedback and analytics.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from psycopg.rows import dict_row

from core.graph import graph_db
from core.config import get_app_config, get_storage_config
from core.context import get_current_tenant_id
from .connection import get_async_connection
from .documents import build_document_stats
from core.observability.logging import get_logger
from .serializers import deserialize_sources, serialize_sources
from .utils import as_iso, now_iso

logger = get_logger(__name__)

_app_config = get_app_config()
_storage_config = get_storage_config()

APP_TIMEZONE = _app_config.timezone
APP_TIMEZONE_NAME = _app_config.app_timezone
POSTGRES_ENABLED = _storage_config.postgres_enabled
ACTIVE_LEARNING_MIN_TOTAL = _app_config.active_learning_min_total
ACTIVE_LEARNING_MAX_POSITIVE_RATE = _app_config.active_learning_max_positive_rate
ACTIVE_LEARNING_LIMIT = _app_config.active_learning_limit


def _now_iso() -> str:
    """Returns the current timestamp in ISO 8601 format in the configured timezone."""
    return now_iso(APP_TIMEZONE)


def _as_iso(value: Any) -> Optional[str]:
    """Converts datetime/str to ISO 8601, omitting null values."""
    return as_iso(value, APP_TIMEZONE)


async def insert_feedback(
    query: str,
    answer: str,
    feedback: str,
    *,
    conversation_id: Optional[str] = None,
    sources: Optional[Iterable[Dict[str, Any]]] = None,
    comment: Optional[str] = None,
) -> None:
    """
    Inserts a new feedback into the database, including any additional metadata.
    """

    sanitized_comment = comment.strip() if isinstance(comment, str) else None
    tenant_id = get_current_tenant_id()

    async with get_async_connection() as conn:
        async with conn.cursor() as cursor:
            await cursor.execute(
                """
                INSERT INTO chat_feedback (query, answer, feedback, conversation_id, sources, comment, tenant_id, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    query,
                    answer,
                    feedback,
                    conversation_id or None,
                    serialize_sources(sources),
                    sanitized_comment,
                    tenant_id,
                    _now_iso(),
                ),
            )
        # conn.commit() is usually handled by autocommit in the pool config

    # Update the graph with feedback counters for the involved documents (if present)
    try:
        doc_ids: Set[str] = set()
        for src in sources or []:
            if not isinstance(src, dict):
                continue
            doc_id = src.get("document_id")
            if isinstance(doc_id, str) and doc_id.strip():
                doc_ids.add(doc_id.strip())
        for doc_id in doc_ids:
            # TODO: Convert graph_db to async or run in thread
            # For now, this call is likely sync/network call.
            # Assuming graph_db client handles its own concurrency or is fast enough for now.
            # Ideally this should be async too.
            graph_db.record_document_feedback(doc_id, feedback, sanitized_comment)
    except Exception as e:
        # Silent: doesn't block feedback collection if graph is disabled/unreachable
        logger.warning(f"Failed to record document feedback in graph: {e}")


async def get_feedbacks(
    feedback: Optional[str] = None,
    *,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieves feedback from the database.
    If `feedback` is specified ('positive' or 'negative'), returns only filtered ones.
    It is possible to limit the number of records returned with `limit`.
    """

    async with get_async_connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SET TIME ZONE %s", (APP_TIMEZONE_NAME,))
            tenant_id = get_current_tenant_id()
            query = (
                "SELECT id, query, answer, feedback, conversation_id, sources, comment, timestamp "
                "FROM chat_feedback WHERE tenant_id = %s"
            )
            params: List[Any] = [tenant_id]
            if feedback:
                query += " AND feedback = %s"
                params.append(feedback)
            query += " ORDER BY timestamp DESC"

            if limit is not None and limit > 0:
                query += " LIMIT %s"
                params.append(limit)

            await cursor.execute(query, params)
            rows = await cursor.fetchall()

    results: List[Dict[str, Any]] = []
    for row in rows:
        timestamp_value = _as_iso(row.get("timestamp"))
        entry: Dict[str, Any] = {
            "id": row["id"],
            "query": row["query"],
            "answer": row["answer"],
            "feedback": row["feedback"],
            "timestamp": timestamp_value,
        }
        conversation_id = row.get("conversation_id")
        if conversation_id:
            entry["conversation_id"] = conversation_id
        comment_value = row.get("comment")
        if comment_value:
            entry["comment"] = comment_value
        sources = deserialize_sources(row.get("sources"))
        if sources:
            entry["sources"] = sources
        results.append(entry)
    return results


async def get_feedback_analytics(
    *,
    days: Optional[int] = None,
    recent_limit: int = 20,
    top_limit: int = 10,
) -> Dict[str, Any]:
    """
    Returns a feedback summary with:
    - aggregated counts
    - daily time series
    - last feedback received
    - most frequent queries
    - most cited documents/sources in responses with feedback
    """

    total = 0
    positives = 0
    negatives = 0
    since_iso: Optional[str] = None
    timeseries: List[Dict[str, Any]] = []
    recent: List[Dict[str, Any]] = []
    top_queries: List[Dict[str, Any]] = []
    doc_rows: List[Dict[str, Any]] = []
    learning_rows: List[Dict[str, Any]] = []

    async with get_async_connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SET TIME ZONE %s", (APP_TIMEZONE_NAME,))

            tenant_id = get_current_tenant_id()
            params: List[Any] = [tenant_id]
            where_conditions = ["tenant_id = %s"]

            if days is not None:
                since = datetime.datetime.now(APP_TIMEZONE) - datetime.timedelta(
                    days=max(1, days)
                )
                since_iso = since.isoformat()
                where_conditions.append("timestamp >= %s")
                params.append(since)

            where_clause = "WHERE " + " AND ".join(where_conditions)
            base_params = tuple(params)

            totals_query = (
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
                "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives "
                f"FROM chat_feedback {where_clause}"  # nosec B608
            )
            await cursor.execute(totals_query, base_params)
            totals_row = await cursor.fetchone() or {}
            total = int(totals_row.get("total") or 0)
            positives = int(totals_row.get("positives") or 0)
            negatives = int(totals_row.get("negatives") or 0)

            timeseries_query = (
                "SELECT DATE(timestamp) AS day, "
                "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
                "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives, "
                "COUNT(*) AS total "
                f"FROM chat_feedback {where_clause} "  # nosec B608
                "GROUP BY day "
                "ORDER BY day ASC"
            )
            await cursor.execute(timeseries_query, base_params)
            for row in await cursor.fetchall():
                day_value = row.get("day")
                if hasattr(day_value, "isoformat"):
                    day_str = day_value.isoformat()  # type: ignore[union-attr]
                else:
                    day_str = str(day_value)
                timeseries.append(
                    {
                        "date": day_str,
                        "total": int(row.get("total") or 0),
                        "positives": int(row.get("positives") or 0),
                        "negatives": int(row.get("negatives") or 0),
                    }
                )

            recent_query = (
                "SELECT id, query, answer, feedback, conversation_id, sources, comment, timestamp "
                f"FROM chat_feedback {where_clause} "  # nosec B608
                "ORDER BY timestamp DESC "
                "LIMIT %s"
            )
            recent_params = list(base_params)
            recent_params.append(max(1, recent_limit))
            await cursor.execute(recent_query, recent_params)
            for row in await cursor.fetchall():
                entry: Dict[str, Any] = {
                    "id": row["id"],
                    "query": row["query"],
                    "answer": row["answer"],
                    "feedback": row["feedback"],
                    "timestamp": _as_iso(row.get("timestamp")),
                }
                if row.get("conversation_id"):
                    entry["conversation_id"] = row["conversation_id"]
                comment_value = row.get("comment")
                if comment_value:
                    entry["comment"] = comment_value
                sources = deserialize_sources(row.get("sources"))
                if sources:
                    entry["sources"] = sources
                recent.append(entry)

            top_queries_query = (
                "SELECT query, "
                "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
                "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives, "
                "COUNT(*) AS total, "
                "MAX(timestamp) AS last_timestamp "
                f"FROM chat_feedback {where_clause} "  # nosec B608
                "GROUP BY query "
                "HAVING COUNT(*) > 0 "
                "ORDER BY total DESC, last_timestamp DESC "
                "LIMIT %s"
            )
            top_queries_params = list(base_params)
            top_queries_params.append(max(1, top_limit))
            await cursor.execute(top_queries_query, top_queries_params)
            for row in await cursor.fetchall():
                total_count = int(row.get("total") or 0)
                positive_count = int(row.get("positives") or 0)
                negative_count = int(row.get("negatives") or 0)
                top_queries.append(
                    {
                        "query": row["query"],
                        "total": total_count,
                        "positives": positive_count,
                        "negatives": negative_count,
                        "positive_rate": (positive_count / total_count)
                        if total_count
                        else 0.0,
                        "last_timestamp": _as_iso(row.get("last_timestamp")),
                    }
                )

            if where_clause:
                doc_query = (
                    "SELECT feedback, sources, timestamp FROM chat_feedback "
                    f"{where_clause} AND sources IS NOT NULL"  # nosec B608
                )
                doc_params = base_params
            else:
                doc_query = (
                    "SELECT feedback, sources, timestamp FROM chat_feedback "
                    "WHERE sources IS NOT NULL"
                )
                doc_params = ()
            await cursor.execute(doc_query, doc_params)
            doc_rows = await cursor.fetchall()

            learning_query = (
                "SELECT query, "
                "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
                "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives, "
                "COUNT(*) AS total, "
                "MAX(timestamp) AS last_timestamp "
                f"FROM chat_feedback {where_clause} "  # nosec B608
                "GROUP BY query "
                "HAVING COUNT(*) >= %s "
                "ORDER BY negatives DESC, total DESC, last_timestamp DESC "
                "LIMIT %s"
            )
            learning_params = list(base_params)
            learning_params.append(max(1, ACTIVE_LEARNING_MIN_TOTAL))
            learning_params.append(max(1, ACTIVE_LEARNING_LIMIT))
            await cursor.execute(learning_query, learning_params)
            learning_rows = await cursor.fetchall()

    document_stats, _ = build_document_stats(doc_rows)

    top_documents = sorted(
        document_stats.values(),
        key=lambda item: item["total"],
        reverse=True,
    )[: max(1, top_limit)]

    for entry in top_documents:
        total_count = entry["total"] or 0
        if total_count:
            entry["positive_rate"] = entry["positives"] / total_count
            entry["negative_rate"] = entry["negatives"] / total_count
        else:
            entry["positive_rate"] = 0.0
            entry["negative_rate"] = 0.0
        entry["last_timestamp"] = _as_iso(entry.get("last_timestamp"))

    learning_candidates: List[Dict[str, Any]] = []
    for row in learning_rows:
        total_count = int(row["total"] or 0)
        if total_count <= 0:
            continue
        positive_count = int(row["positives"] or 0)
        negative_count = int(row["negatives"] or 0)
        positive_rate = positive_count / total_count if total_count else 0.0
        if (
            positive_rate > ACTIVE_LEARNING_MAX_POSITIVE_RATE
            and negative_count <= positive_count
        ):
            continue
        learning_candidates.append(
            {
                "query": row["query"],
                "total": total_count,
                "positives": positive_count,
                "negatives": negative_count,
                "positive_rate": positive_rate,
                "negative_rate": (negative_count / total_count if total_count else 0.0),
                "last_timestamp": _as_iso(row.get("last_timestamp")),
            }
        )

    return {
        "total_feedbacks": total,
        "positives": positives,
        "negatives": negatives,
        "positive_rate": (positives / total) if total else 0.0,
        "negative_rate": (negatives / total) if total else 0.0,
        "timeseries": timeseries,
        "recent": recent,
        "top_queries": top_queries,
        "top_documents": top_documents,
        "learning_candidates": learning_candidates,
        "window": {"days": days, "since": since_iso},
    }
