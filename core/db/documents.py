"""
Document Database Access.

Provides functions for building and retrieving document-level feedback statistics.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row

from core.cache.local_cache import TTLCache
from core.cache.single_flight import SingleFlight
from core.config import get_app_config, get_storage_config
from core.context import get_current_tenant_id
from core.resilience.retry import retry

from .connection import get_async_connection
from .serializers import deserialize_sources
from .utils import as_iso

_app_config = get_app_config()
_storage_config = get_storage_config()

APP_TIMEZONE = _app_config.timezone
POSTGRES_ENABLED = _storage_config.postgres_enabled
ANALYTICS_DEFAULT_DAYS = _app_config.feedback_analytics_default_days
ANALYTICS_DOC_SCAN_LIMIT = _app_config.feedback_analytics_doc_scan_limit
SUMMARY_CACHE_TTL = _app_config.feedback_summary_cache_ttl

# Only transient connection faults are worth re-running: a schema or SQL error
# is deterministic, so retrying it just multiplies the latency and the pool
# checkouts before failing the request anyway.
RETRYABLE_DB_ERRORS = (psycopg.OperationalError, psycopg.InterfaceError)

# The rollup sits on the RAG hot path (retrieval_scoring.apply_feedback) and
# aggregates up to ANALYTICS_DOC_SCAN_LIMIT rows in Python. Cache it per
# (tenant, min_total) for a short TTL, and coalesce concurrent misses so a
# cold cache under load triggers one scan instead of one per request.
_summary_cache: TTLCache[str, dict[str, dict[str, Any]]] | None = (
    TTLCache(
        maxsize=256, ttl=SUMMARY_CACHE_TTL, metrics_name="document_feedback_summary"
    )
    if SUMMARY_CACHE_TTL > 0
    else None
)
_summary_single_flight: SingleFlight[dict[str, dict[str, Any]]] = SingleFlight()


def _as_iso(value: Any) -> str | None:
    """Converts datetime/str to ISO 8601, omitting null values."""
    return as_iso(value, APP_TIMEZONE)


def determine_primary_key(source: dict[str, Any]) -> str | None:
    """Returns the canonical key for a document source."""

    doc_id = source.get("document_id")
    if isinstance(doc_id, str):
        doc_id = doc_id.strip()
        if doc_id:
            return f"id::{doc_id}"

    path = source.get("path")
    if isinstance(path, str):
        path = path.strip()
        if path:
            return f"path::{path}"

    url = source.get("url")
    if isinstance(url, str):
        url = url.strip()
        if url:
            return f"url::{url}"

    return None


def collect_alias_keys(source: dict[str, Any]) -> list[str]:
    """Returns all usable keys to identify the source."""

    aliases: list[str] = []
    doc_id = source.get("document_id")
    if isinstance(doc_id, str):
        doc_id = doc_id.strip()
        if doc_id:
            aliases.append(f"id::{doc_id}")
    path = source.get("path")
    if isinstance(path, str):
        path = path.strip()
        if path:
            aliases.append(f"path::{path}")
    url = source.get("url")
    if isinstance(url, str):
        url = url.strip()
        if url:
            aliases.append(f"url::{url}")
    return aliases


def build_document_stats(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """
    Aggregates statistics per document from raw feedback entries.

    Returns:
        stats: mapping of canonical key -> aggregate
        aliases: mapping of alias -> canonical key
    """

    stats: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}

    for row in rows:
        feedback_value = row.get("feedback")
        timestamp = row.get("timestamp")
        sources = deserialize_sources(row.get("sources"))
        if not sources:
            continue

        for source in sources:
            primary_key = determine_primary_key(source)
            if not primary_key:
                continue

            entry = stats.get(primary_key)
            if entry is None:
                entry = {
                    "document_id": source.get("document_id"),
                    "title": source.get("title"),
                    "path": source.get("path"),
                    "url": source.get("url"),
                    "origin": source.get("origin"),
                    "source_type": source.get("source_type"),
                    "positives": 0,
                    "negatives": 0,
                    "total": 0,
                    "last_timestamp": None,
                }
                stats[primary_key] = entry

            if feedback_value == "positive":
                entry["positives"] += 1
            elif feedback_value == "negative":
                entry["negatives"] += 1
            entry["total"] += 1

            timestamp_iso = _as_iso(timestamp)
            if timestamp_iso:
                previous = entry.get("last_timestamp")
                if previous is None or timestamp_iso > previous:
                    entry["last_timestamp"] = timestamp_iso

            for alias in collect_alias_keys(source):
                aliases[alias] = primary_key

    for primary_key in stats:
        aliases.setdefault(primary_key, primary_key)

    return stats, aliases


@retry(
    max_attempts=3,
    base_delay=0.5,
    exponential_base=2.0,
    retryable_exceptions=RETRYABLE_DB_ERRORS,
)
async def fetch_document_feedback_rows(
    tenant_id: str, since: datetime.datetime
) -> Sequence[Mapping[str, Any]]:
    """Reads the feedback rows cited by documents for a tenant since ``since``."""

    async with (
        get_async_connection() as conn,
        conn.cursor(row_factory=dict_row) as cursor,
    ):
        # ``feedback`` (interaction scoring) carries no sources; the document
        # citations live on ``chat_feedback`` — same query as the doc rollup in
        # core.db.feedback.get_feedback_analytics.
        await cursor.execute(
            "SELECT feedback, sources, timestamp FROM chat_feedback "
            "WHERE sources IS NOT NULL AND tenant_id = %s AND timestamp >= %s "
            "ORDER BY timestamp DESC LIMIT %s",
            (tenant_id, since, ANALYTICS_DOC_SCAN_LIMIT),
        )
        rows: Sequence[Mapping[str, Any]] = await cursor.fetchall()
    # The pool opens connections with autocommit=True (see connection.py), so a
    # read needs no explicit commit or rollback here.
    return rows


async def get_document_feedback_summary(
    min_total: int = 0,
) -> dict[str, dict[str, Any]]:
    """
    Returns aggregated statistics for each document cited in feedback entries.

    Keys include both the canonical one (e.g. id::abc123) and aliases (path::, url::).

    The result is cached per (tenant, min_total) for
    ``FEEDBACK_SUMMARY_CACHE_TTL`` seconds; callers must treat it as read-only,
    since concurrent callers share the same mapping.
    """

    if not POSTGRES_ENABLED:
        return {}

    tenant_id = get_current_tenant_id()
    cache_key = f"{tenant_id}:{min_total}"

    if _summary_cache is not None:
        cached = await _summary_cache.get(cache_key)
        if cached is not None:
            return cached
        return await _summary_single_flight.do(
            cache_key, lambda: _build_summary(tenant_id, min_total, cache_key)
        )

    return await _build_summary(tenant_id, min_total, cache_key)


async def _build_summary(
    tenant_id: str, min_total: int, cache_key: str
) -> dict[str, dict[str, Any]]:
    """Queries and aggregates the rollup, populating the cache when enabled."""

    if _summary_cache is not None:
        # Another caller may have filled the cache while this one waited for
        # the single-flight slot.
        cached = await _summary_cache.get(cache_key)
        if cached is not None:
            return cached

    # Always bound the scan by a time window and a hard row cap so this never
    # pulls the full table; build_document_stats then aggregates in Python.
    since = datetime.datetime.now(APP_TIMEZONE) - datetime.timedelta(
        days=ANALYTICS_DEFAULT_DAYS
    )

    rows = await fetch_document_feedback_rows(tenant_id, since)

    document_stats, alias_map = build_document_stats(rows)

    summary: dict[str, dict[str, Any]] = {}
    for primary_key, entry in document_stats.items():
        if min_total and entry["total"] < min_total:
            continue
        total_count = entry["total"] or 0
        computed = {
            "document_id": entry.get("document_id"),
            "title": entry.get("title"),
            "path": entry.get("path"),
            "url": entry.get("url"),
            "origin": entry.get("origin"),
            "source_type": entry.get("source_type"),
            "positives": entry.get("positives", 0),
            "negatives": entry.get("negatives", 0),
            "total": total_count,
            "positive_rate": (entry["positives"] / total_count if total_count else 0.0),
            "negative_rate": (entry["negatives"] / total_count if total_count else 0.0),
            "last_timestamp": entry.get("last_timestamp"),
        }
        summary[primary_key] = computed

    for alias, primary in alias_map.items():
        base = summary.get(primary)
        if base is not None:
            summary[alias] = base

    if _summary_cache is not None:
        await _summary_cache.set(cache_key, summary)

    return summary
