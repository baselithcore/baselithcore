"""
Document Database Access.

Provides functions for building and retrieving document-level feedback statistics.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from psycopg.rows import dict_row

from core.config import get_app_config, get_storage_config
from core.context import get_current_tenant_id
from .connection import get_async_connection
from .serializers import deserialize_sources
from .utils import as_iso

_app_config = get_app_config()
_storage_config = get_storage_config()

APP_TIMEZONE = _app_config.timezone
APP_TIMEZONE_NAME = _app_config.app_timezone
POSTGRES_ENABLED = _storage_config.postgres_enabled


def _as_iso(value: Any) -> Optional[str]:
    """Converts datetime/str to ISO 8601, omitting null values."""
    return as_iso(value, APP_TIMEZONE)


def determine_primary_key(source: Dict[str, Any]) -> Optional[str]:
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


def collect_alias_keys(source: Dict[str, Any]) -> List[str]:
    """Returns all usable keys to identify the source."""

    aliases: List[str] = []
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
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    Aggregates statistics per document from raw feedback entries.

    Returns:
        stats: mapping of canonical key -> aggregate
        aliases: mapping of alias -> canonical key
    """

    stats: Dict[str, Dict[str, Any]] = {}
    aliases: Dict[str, str] = {}

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


async def get_document_feedback_summary(
    min_total: int = 0,
) -> Dict[str, Dict[str, Any]]:
    """
    Returns aggregated statistics for each document cited in feedback entries.

    Keys include both the canonical one (e.g. id::abc123) and aliases (path::, url::).
    """

    if not POSTGRES_ENABLED:
        return {}

    async with get_async_connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute("SET TIME ZONE %s", (APP_TIMEZONE_NAME,))
            tenant_id = get_current_tenant_id()
            await cursor.execute(
                "SELECT feedback, sources, timestamp FROM feedback WHERE sources IS NOT NULL AND tenant_id = %s",
                (tenant_id,),
            )
            rows = await cursor.fetchall()
        # conn.rollback() is automatic/not needed with async context manager usually, but let's check connection.py implementation.
        # AsyncConnectionPool usually commits/rollbacks.
        # Looking at connection.py: "kwargs={'autocommit': True}". So no explicit rollback needed for simple reads.

    document_stats, alias_map = build_document_stats(rows)

    summary: Dict[str, Dict[str, Any]] = {}
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

    return summary
