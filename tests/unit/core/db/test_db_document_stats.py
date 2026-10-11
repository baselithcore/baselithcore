"""Pure helpers behind the feedback rollups: source (de)serialization, ISO
timestamps, document keys and the per-document aggregation, plus the summary
cache paths of :func:`get_document_feedback_summary`."""

from __future__ import annotations

import datetime
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from baselith.core.db import documents
from baselith.core.db.serializers import deserialize_sources, serialize_sources
from baselith.core.db.utils import as_iso, now_iso

ROME = ZoneInfo("Europe/Rome")

# --------------------------------------------------------------------------- #
# serializers
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("empty", [None, [], ()])
def test_serialize_nothing_is_null(empty) -> None:
    assert serialize_sources(empty) is None


def test_serialize_keeps_only_known_truthy_fields() -> None:
    raw = serialize_sources(
        [
            {
                "document_id": " d1 ",
                "title": "",
                "path": "   ",
                "url": "https://x",
                "origin": None,
                "source_type": "web",
                "score": "0.25",
                "secret": "dropped",
            },
            {"title": "bad score", "score": "high"},
            {"score": None},
            "not-a-dict",
        ]
    )

    assert json.loads(raw) == [
        {"document_id": "d1", "url": "https://x", "source_type": "web", "score": 0.25},
        {"title": "bad score"},
    ]


def test_serialize_returns_null_when_every_entry_is_empty() -> None:
    assert serialize_sources([{"title": " "}, {"other": 1}]) is None


def test_serialize_returns_null_for_unencodable_values() -> None:
    assert serialize_sources([{"document_id": object()}]) is None


@pytest.mark.parametrize("raw", [None, "", "{not json", "42", '{"a": 1}'])
def test_deserialize_garbage_is_an_empty_list(raw) -> None:
    assert deserialize_sources(raw) == []


def test_deserialize_normalizes_like_serialize() -> None:
    raw = json.dumps(
        [
            {"document_id": " d ", "score": "1", "title": ""},
            {"path": "p", "score": "nan-ish"},
            {"unknown": "x"},
            ["not", "a", "dict"],
        ]
    )
    assert deserialize_sources(raw) == [
        {"document_id": "d", "score": 1.0},
        {"path": "p"},
    ]


def test_round_trip_is_stable() -> None:
    sources = [{"document_id": "a", "title": "T", "score": 0.5}]
    assert deserialize_sources(serialize_sources(sources)) == sources


# --------------------------------------------------------------------------- #
# utils
# --------------------------------------------------------------------------- #


def test_as_iso_handles_every_input_shape() -> None:
    naive = datetime.datetime(2026, 1, 1, 12, 0)  # noqa: DTZ001 - naive on purpose
    aware = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)

    assert as_iso(None) is None
    assert as_iso(naive) == "2026-01-01T12:00:00"
    # Naive values are *labelled* with the zone, not shifted.
    assert as_iso(naive, ROME) == "2026-01-01T12:00:00+01:00"
    # Aware values are converted.
    assert as_iso(aware, ROME) == "2026-01-01T13:00:00+01:00"
    assert as_iso(aware) == "2026-01-01T12:00:00+00:00"
    assert as_iso(datetime.date(2024, 1, 31)) == "2024-01-31"
    assert as_iso("already") == "already"
    assert as_iso(17) == "17"


def test_now_iso_is_timezone_aware() -> None:
    assert datetime.datetime.fromisoformat(now_iso()).utcoffset() == datetime.timedelta(
        0
    )
    assert datetime.datetime.fromisoformat(now_iso(ROME)).tzinfo is not None


# --------------------------------------------------------------------------- #
# document keys and aggregation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("source", "key", "aliases"),
    [
        (
            {"document_id": " d ", "path": " p ", "url": " u "},
            "id::d",
            ["id::d", "path::p", "url::u"],
        ),
        ({"document_id": "  ", "path": "p"}, "path::p", ["path::p"]),
        ({"path": "", "url": "https://u"}, "url::https://u", ["url::https://u"]),
        ({"document_id": 5, "path": None, "url": "  "}, None, []),
    ],
)
def test_primary_key_and_aliases(source, key, aliases) -> None:
    assert documents.determine_primary_key(source) == key
    assert documents.collect_alias_keys(source) == aliases


def test_build_document_stats_counts_and_keeps_the_latest_timestamp() -> None:
    early = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    late = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)
    src = json.dumps([{"document_id": "d", "path": "docs/d.md", "title": "D"}])
    rows = [
        {"feedback": "positive", "sources": src, "timestamp": late},
        {"feedback": "negative", "sources": src, "timestamp": early},
        {"feedback": "neutral", "sources": src, "timestamp": None},
        {"feedback": "positive", "sources": None, "timestamp": late},
        {"feedback": "positive", "sources": json.dumps([{"title": "keyless"}])},
    ]

    stats, aliases = documents.build_document_stats(rows)

    assert list(stats) == ["id::d"]
    entry = stats["id::d"]
    assert (entry["positives"], entry["negatives"], entry["total"]) == (1, 1, 3)
    assert entry["title"] == "D"
    assert entry["last_timestamp"] == documents._as_iso(late)
    assert aliases == {"id::d": "id::d", "path::docs/d.md": "id::d"}


# --------------------------------------------------------------------------- #
# get_document_feedback_summary paths not covered elsewhere
# --------------------------------------------------------------------------- #


def _install_rows(monkeypatch, rows: list[dict[str, Any]]) -> AsyncMock:
    cursor = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=rows)

    @asynccontextmanager
    async def cursor_cm(*_a, **_k):
        yield cursor

    conn = MagicMock()
    conn.cursor = MagicMock(side_effect=cursor_cm)

    @asynccontextmanager
    async def get_conn():
        yield conn

    monkeypatch.setattr(documents, "get_async_connection", get_conn)
    monkeypatch.setattr(documents, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(documents, "get_current_tenant_id", lambda: "t-docs")
    return cursor


_ROWS = [
    {"feedback": "positive", "sources": '[{"document_id": "hot", "url": "u"}]'},
    {"feedback": "positive", "sources": '[{"document_id": "hot"}]'},
    {"feedback": "negative", "sources": '[{"document_id": "cold", "path": "c"}]'},
]


async def test_summary_is_empty_when_postgres_is_disabled(monkeypatch) -> None:
    cursor = _install_rows(monkeypatch, _ROWS)
    monkeypatch.setattr(documents, "POSTGRES_ENABLED", False)

    assert await documents.get_document_feedback_summary() == {}
    cursor.execute.assert_not_awaited()


async def test_min_total_drops_documents_and_their_aliases(monkeypatch) -> None:
    _install_rows(monkeypatch, _ROWS)
    monkeypatch.setattr(documents, "_summary_cache", None)

    summary = await documents.get_document_feedback_summary(min_total=2)

    assert set(summary) == {"id::hot", "url::u"}
    hot = summary["id::hot"]
    assert (hot["total"], hot["positive_rate"], hot["negative_rate"]) == (2, 1.0, 0.0)
    assert summary["url::u"] is hot


async def test_without_a_cache_every_call_queries(monkeypatch) -> None:
    cursor = _install_rows(monkeypatch, [])
    monkeypatch.setattr(documents, "_summary_cache", None)

    await documents.get_document_feedback_summary()
    await documents.get_document_feedback_summary()

    assert cursor.execute.await_count == 2
    sql, params = cursor.execute.await_args.args
    assert params[0] == "t-docs"
    assert params[2] == documents.ANALYTICS_DOC_SCAN_LIMIT


async def test_build_summary_reuses_a_value_cached_while_it_waited(
    monkeypatch,
) -> None:
    """A caller queued on the single-flight slot must not rescan."""
    cursor = _install_rows(monkeypatch, _ROWS)
    cached = {"id::x": {"total": 9}}
    cache = MagicMock()
    cache.get = AsyncMock(return_value=cached)
    cache.set = AsyncMock()
    monkeypatch.setattr(documents, "_summary_cache", cache)

    assert await documents._build_summary("t-docs", 0, "t-docs:0") is cached
    cursor.execute.assert_not_awaited()
    cache.set.assert_not_awaited()


async def test_build_summary_populates_the_cache(monkeypatch) -> None:
    _install_rows(monkeypatch, _ROWS)
    cache = MagicMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock()
    monkeypatch.setattr(documents, "_summary_cache", cache)

    summary = await documents._build_summary("t-docs", 0, "t-docs:0")

    cache.set.assert_awaited_once_with("t-docs:0", summary)
    assert summary["path::c"] is summary["id::cold"]
