"""
Feedback Database Access.

Provides functions for inserting and retrieving chat feedback and analytics.
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import Iterable
from typing import Any

from psycopg import sql
from psycopg.rows import dict_row

from core.config import get_app_config, get_storage_config
from core.context import get_current_tenant_id
from core.graph import graph_db
from core.observability.logging import get_logger
from core.resilience.retry import retry

from .connection import get_async_connection
from .documents import build_document_stats
from .serializers import deserialize_sources, serialize_sources
from .utils import as_iso, now_iso

logger = get_logger(__name__)

_app_config = get_app_config()
_storage_config = get_storage_config()

APP_TIMEZONE = _app_config.timezone
POSTGRES_ENABLED = _storage_config.postgres_enabled
ACTIVE_LEARNING_MIN_TOTAL = _app_config.active_learning_min_total
ACTIVE_LEARNING_MAX_POSITIVE_RATE = _app_config.active_learning_max_positive_rate
ACTIVE_LEARNING_LIMIT = _app_config.active_learning_limit
ANALYTICS_DEFAULT_DAYS = _app_config.feedback_analytics_default_days
ANALYTICS_DOC_SCAN_LIMIT = _app_config.feedback_analytics_doc_scan_limit


def _now_iso() -> str:
    """Returns the current timestamp in ISO 8601 format in the configured timezone."""
    return now_iso(APP_TIMEZONE)


def _as_iso(value: Any) -> str | None:
    """Converts datetime/str to ISO 8601, omitting null values."""
    return as_iso(value, APP_TIMEZONE)


@retry(max_attempts=3, base_delay=0.5, exponential_base=2.0)
async def insert_feedback(
    query: str,
    answer: str,
    feedback: str,
    *,
    conversation_id: str | None = None,
    sources: Iterable[dict[str, Any]] | None = None,
    comment: str | None = None,
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
        doc_ids: set[str] = set()
        for src in sources or []:
            if not isinstance(src, dict):
                continue
            doc_id = src.get("document_id")
            if isinstance(doc_id, str) and doc_id.strip():
                doc_ids.add(doc_id.strip())
        if doc_ids:
            graph_timeout = float(getattr(_app_config, "graph_feedback_timeout", 5.0))
            doc_ids_list = list(doc_ids)

            async def _record_with_timeout(doc_id: str) -> None:
                await asyncio.wait_for(
                    asyncio.to_thread(
                        graph_db.record_document_feedback,
                        doc_id,
                        feedback,
                        sanitized_comment,
                    ),
                    timeout=graph_timeout,
                )

            results = await asyncio.gather(
                *[_record_with_timeout(doc_id) for doc_id in doc_ids_list],
                return_exceptions=True,
            )
            for doc_id, result in zip(doc_ids_list, results):
                if isinstance(result, BaseException):
                    logger.warning(
                        "graph_feedback_record_failed",
                        extra={"document_id": doc_id, "error": str(result)},
                    )
    except Exception as e:
        # Silent: doesn't block feedback collection if graph is disabled/unreachable
        logger.warning(f"Failed to record document feedback in graph: {e}")


@retry(max_attempts=3, base_delay=0.5, exponential_base=2.0)
async def get_feedbacks(
    feedback: str | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieves feedback from the database.
    If `feedback` is specified ('positive' or 'negative'), returns only filtered ones.
    It is possible to limit the number of records returned with `limit`.
    """

    async with (
        get_async_connection() as conn,
        conn.cursor(row_factory=dict_row) as cursor,
    ):
        await cursor.execute("SET statement_timeout = '30s'")
        tenant_id = get_current_tenant_id()
        query = (
            "SELECT id, query, answer, feedback, conversation_id, sources, comment, timestamp "
            "FROM chat_feedback WHERE tenant_id = %s"
        )
        params: list[Any] = [tenant_id]
        if feedback:
            query += " AND feedback = %s"
            params.append(feedback)
        query += " ORDER BY timestamp DESC"

        if limit is not None and limit > 0:
            query += " LIMIT %s"
            params.append(limit)

        await cursor.execute(query, params)
        rows = await cursor.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        timestamp_value = _as_iso(row.get("timestamp"))
        entry: dict[str, Any] = {
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


@retry(max_attempts=3, base_delay=0.5, exponential_base=2.0)
async def get_feedback_analytics(
    *,
    days: int | None = None,
    recent_limit: int = 20,
    top_limit: int = 10,
) -> dict[str, Any]:
    """
    Returns a feedback summary with:
    - aggregated counts
    - daily time series
    - last feedback received
    - most frequent queries
    - most cited documents/sources in responses with feedback

    A time window is always applied: ``days`` when provided, otherwise a
    configurable default (``feedback_analytics_default_days``) so no query
    scans the full table. The per-document rollup keeps Python aggregation
    (via ``build_document_stats``) but is bounded by the window plus a hard
    row cap (``feedback_analytics_doc_scan_limit``).
    """

    total = 0
    positives = 0
    negatives = 0
    since_iso: str | None = None
    timeseries: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    top_queries: list[dict[str, Any]] = []
    doc_rows: list[dict[str, Any]] = []
    learning_rows: list[dict[str, Any]] = []

    # Cap how many of the six aggregations hold a pooled connection at once.
    # Firing all six unbounded takes 6 of the pool's ~20 connections per
    # dashboard hit — a few concurrent dashboards exhaust the pool and unrelated
    # requests queue on DB_POOL_TIMEOUT. Two-at-a-time keeps most of the
    # concurrency win while bounding pool pressure. statement_timeout is already
    # baked into the pool options (connection.py), so no per-cursor SET is needed
    # (setting the session GUC on an autocommit pooled connection would also leak
    # to the next checkout).
    fetch_gate = asyncio.Semaphore(2)

    async def _fetch_all(
        query: sql.Composed, query_params: Iterable[Any]
    ) -> list[dict[str, Any]]:
        """Run one analytics query on its own pooled connection."""
        async with fetch_gate:
            async with get_async_connection() as conn:
                async with conn.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(query, list(query_params))
                    return await cursor.fetchall()

    tenant_id = get_current_tenant_id()
    params: list[Any] = [tenant_id]
    where_fragments: list[sql.Composable] = [sql.SQL("tenant_id = %s")]

    # Always bound by a time window: when no explicit range is requested fall
    # back to a configurable default so analytics never scan the whole table.
    effective_days = max(1, days) if days is not None else ANALYTICS_DEFAULT_DAYS
    since = datetime.datetime.now(APP_TIMEZONE) - datetime.timedelta(
        days=effective_days
    )
    since_iso = since.isoformat()
    where_fragments.append(sql.SQL("timestamp >= %s"))
    params.append(since)

    where_clause = sql.SQL("WHERE ") + sql.SQL(" AND ").join(where_fragments)
    base_params = tuple(params)

    totals_query = sql.SQL(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
        "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives "
        "FROM chat_feedback {where}"
    ).format(where=where_clause)

    timeseries_query = sql.SQL(
        "SELECT DATE(timestamp) AS day, "
        "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
        "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives, "
        "COUNT(*) AS total "
        "FROM chat_feedback {where} "
        "GROUP BY day "
        "ORDER BY day ASC"
    ).format(where=where_clause)

    recent_query = sql.SQL(
        "SELECT id, query, answer, feedback, conversation_id, sources, comment, timestamp "
        "FROM chat_feedback {where} "
        "ORDER BY timestamp DESC "
        "LIMIT %s"
    ).format(where=where_clause)

    top_queries_query = sql.SQL(
        "SELECT query, "
        "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
        "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives, "
        "COUNT(*) AS total, "
        "MAX(timestamp) AS last_timestamp "
        "FROM chat_feedback {where} "
        "GROUP BY query "
        "HAVING COUNT(*) > 0 "
        "ORDER BY total DESC, last_timestamp DESC "
        "LIMIT %s"
    ).format(where=where_clause)

    # Bound the per-document scan: only the most recent rows (within the time
    # window) cross the wire, then build_document_stats aggregates them in
    # Python. See the note in get_feedback_analytics' docstring/PR for why the
    # rollup stays in Python rather than SQL.
    doc_query = sql.SQL(
        "SELECT feedback, sources, timestamp FROM chat_feedback "
        "{where} AND sources IS NOT NULL "
        "ORDER BY timestamp DESC "
        "LIMIT %s"
    ).format(where=where_clause)

    learning_query = sql.SQL(
        "SELECT query, "
        "SUM(CASE WHEN feedback='positive' THEN 1 ELSE 0 END) AS positives, "
        "SUM(CASE WHEN feedback='negative' THEN 1 ELSE 0 END) AS negatives, "
        "COUNT(*) AS total, "
        "MAX(timestamp) AS last_timestamp "
        "FROM chat_feedback {where} "
        "GROUP BY query "
        "HAVING COUNT(*) >= %s "
        "ORDER BY negatives DESC, total DESC, last_timestamp DESC "
        "LIMIT %s"
    ).format(where=where_clause)

    # The six aggregations are independent — run them concurrently, each on
    # its own pooled connection, instead of sequentially on a single cursor
    # (dashboard latency drops to the slowest query instead of the sum).
    (
        totals_rows,
        timeseries_rows,
        recent_rows,
        top_queries_rows,
        doc_rows,
        learning_rows,
    ) = await asyncio.gather(
        _fetch_all(totals_query, base_params),
        _fetch_all(timeseries_query, base_params),
        _fetch_all(recent_query, (*base_params, max(1, recent_limit))),
        _fetch_all(top_queries_query, (*base_params, max(1, top_limit))),
        _fetch_all(doc_query, (*base_params, max(1, ANALYTICS_DOC_SCAN_LIMIT))),
        _fetch_all(
            learning_query,
            (
                *base_params,
                max(1, ACTIVE_LEARNING_MIN_TOTAL),
                max(1, ACTIVE_LEARNING_LIMIT),
            ),
        ),
    )

    totals_row = totals_rows[0] if totals_rows else {}
    total = int(totals_row.get("total") or 0)
    positives = int(totals_row.get("positives") or 0)
    negatives = int(totals_row.get("negatives") or 0)

    for row in timeseries_rows:
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

    for row in recent_rows:
        entry: dict[str, Any] = {
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

    for row in top_queries_rows:
        total_count = int(row.get("total") or 0)
        positive_count = int(row.get("positives") or 0)
        negative_count = int(row.get("negatives") or 0)
        top_queries.append(
            {
                "query": row["query"],
                "total": total_count,
                "positives": positive_count,
                "negatives": negative_count,
                "positive_rate": (positive_count / total_count) if total_count else 0.0,
                "last_timestamp": _as_iso(row.get("last_timestamp")),
            }
        )

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

    learning_candidates: list[dict[str, Any]] = []
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
