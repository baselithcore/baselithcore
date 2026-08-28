"""Regression tests for the document feedback rollup on the RAG hot path."""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import psycopg
import pytest

from core.db import documents


@pytest.fixture(autouse=True)
async def clear_summary_cache():
    """The rollup cache is module-level; keep tests independent of each other."""
    if documents._summary_cache is not None:
        await documents._summary_cache.clear()
    yield
    if documents._summary_cache is not None:
        await documents._summary_cache.clear()


def _patch_connection(monkeypatch, cursor):
    @asynccontextmanager
    async def cursor_gen(*args, **kwargs):
        yield cursor

    conn = AsyncMock()
    conn.cursor = MagicMock(side_effect=cursor_gen)

    @asynccontextmanager
    async def get_conn():
        yield conn

    monkeypatch.setattr(documents, "get_async_connection", get_conn)
    monkeypatch.setattr(documents, "POSTGRES_ENABLED", True)


async def test_rollup_queries_chat_feedback_not_feedback(monkeypatch):
    """``feedback`` has no ``feedback``/``sources`` column — those live on
    ``chat_feedback``. Querying the wrong table raised UndefinedColumn on every
    RAG request with FEEDBACK_BOOST_ENABLED on."""
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    _patch_connection(monkeypatch, cursor)

    await documents.get_document_feedback_summary()

    sql = cursor.execute.await_args.args[0]
    assert "FROM chat_feedback" in sql
    assert "FROM feedback " not in sql


async def test_rollup_aggregates_rows_into_summary(monkeypatch):
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(
        return_value=[
            {
                "feedback": "positive",
                "sources": '[{"document_id": "abc", "path": "docs/a.md"}]',
                "timestamp": datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC),
            },
            {
                "feedback": "negative",
                "sources": '[{"document_id": "abc", "path": "docs/a.md"}]',
                "timestamp": datetime.datetime(2026, 1, 2, tzinfo=datetime.UTC),
            },
        ]
    )
    _patch_connection(monkeypatch, cursor)

    summary = await documents.get_document_feedback_summary()

    entry = summary["id::abc"]
    assert (entry["positives"], entry["negatives"], entry["total"]) == (1, 1, 2)
    # Aliases resolve to the same aggregate.
    assert summary["path::docs/a.md"] is entry


async def test_deterministic_sql_error_is_not_retried(monkeypatch):
    """A schema/SQL fault is deterministic: retrying it only multiplies latency
    and pool checkouts before failing the request anyway."""
    cursor = AsyncMock()
    cursor.execute = AsyncMock(side_effect=psycopg.errors.UndefinedColumn("boom"))
    _patch_connection(monkeypatch, cursor)

    with pytest.raises(psycopg.errors.UndefinedColumn):
        await documents.get_document_feedback_summary()

    assert cursor.execute.await_count == 1


async def test_transient_connection_error_is_retried(monkeypatch):
    cursor = AsyncMock()
    cursor.execute = AsyncMock(side_effect=psycopg.OperationalError("gone"))
    _patch_connection(monkeypatch, cursor)

    with pytest.raises(psycopg.OperationalError):
        await documents.get_document_feedback_summary()

    assert cursor.execute.await_count == 3


async def test_repeated_calls_hit_the_cache(monkeypatch):
    """The rollup runs per RAG request; without a cache every request rescans
    up to ANALYTICS_DOC_SCAN_LIMIT rows and re-aggregates them in Python."""
    if documents._summary_cache is None:
        pytest.skip("summary cache disabled by configuration")

    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    _patch_connection(monkeypatch, cursor)

    first = await documents.get_document_feedback_summary()
    second = await documents.get_document_feedback_summary()

    assert cursor.execute.await_count == 1
    assert first is second


async def test_min_total_is_part_of_the_cache_key(monkeypatch):
    if documents._summary_cache is None:
        pytest.skip("summary cache disabled by configuration")

    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    _patch_connection(monkeypatch, cursor)

    await documents.get_document_feedback_summary(min_total=0)
    await documents.get_document_feedback_summary(min_total=5)

    assert cursor.execute.await_count == 2
