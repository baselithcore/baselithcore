"""SQL issued and rows shaped by :mod:`baselith.core.db.feedback`.

The pooled connection is replaced by a recording fake: each test asserts on
the statement and parameters that reach the cursor, and on how the returned
rows are turned into the API payload.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from collections.abc import Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from baselith.core.db import feedback as fb

TENANT = "tenant-a"


class _Recorder:
    """Fake pool: hands out cursors that record SQL and return canned rows."""

    def __init__(self, rows_for: Callable[[str], list[dict[str, Any]]]) -> None:
        self.rows_for = rows_for
        self.calls: list[tuple[str, Any]] = []
        self.cursor_kwargs: list[dict[str, Any]] = []
        self.active = 0
        self.peak = 0
        self.execute_error: Exception | None = None

    @asynccontextmanager
    async def connection(self):
        recorder = self

        class _Cursor:
            async def execute(self, query: Any, params: Any = None) -> None:
                if recorder.execute_error is not None:
                    raise recorder.execute_error
                text = query if isinstance(query, str) else query.as_string(None)
                self._text = text
                recorder.calls.append((text, params))
                # Yield so concurrent fetches overlap if nothing bounds them.
                await asyncio.sleep(0)
                await asyncio.sleep(0)

            async def fetchall(self) -> list[dict[str, Any]]:
                return recorder.rows_for(self._text)

        class _Conn:
            @asynccontextmanager
            async def cursor(self, **kwargs: Any):
                recorder.cursor_kwargs.append(kwargs)
                yield _Cursor()

        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            yield _Conn()
        finally:
            self.active -= 1


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):
    def _install(rows_for: Callable[[str], list[dict[str, Any]]] = lambda _q: []):
        recorder = _Recorder(rows_for)
        monkeypatch.setattr(fb, "get_async_connection", recorder.connection)
        monkeypatch.setattr(fb, "get_current_tenant_id", lambda: TENANT)
        return recorder

    return _install


class _Graph:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.fail_for = fail_for or set()

    def record_document_feedback(
        self, doc_id: str, feedback: str, comment: str | None
    ) -> None:
        self.calls.append((doc_id, feedback, comment))
        if doc_id in self.fail_for:
            raise RuntimeError(f"graph down for {doc_id}")


# --------------------------------------------------------------------------- #
# insert_feedback
# --------------------------------------------------------------------------- #


async def test_insert_writes_one_row_with_sanitized_values(install, monkeypatch):
    recorder = install()
    graph = _Graph()
    monkeypatch.setattr(fb, "graph_db", graph)

    await fb.insert_feedback(
        "q?",
        "a.",
        "positive",
        conversation_id="",
        sources=[{"title": "  T  ", "score": "0.5"}],
        comment="  nice  ",
    )

    ((sql_text, params),) = recorder.calls
    assert sql_text.strip().startswith("INSERT INTO chat_feedback")
    query, answer, feedback, conv, sources, comment, tenant, ts = params
    assert (query, answer, feedback, conv, comment, tenant) == (
        "q?",
        "a.",
        "positive",
        None,
        "nice",
        TENANT,
    )
    assert json.loads(sources) == [{"title": "T", "score": 0.5}]
    assert datetime.datetime.fromisoformat(ts).tzinfo is not None
    # No document id among the sources -> the graph is never contacted.
    assert graph.calls == []


async def test_insert_records_each_cited_document_once_in_the_graph(
    install, monkeypatch, caplog
):
    install()
    graph = _Graph(fail_for={"doc-b"})
    monkeypatch.setattr(fb, "graph_db", graph)

    await fb.insert_feedback(
        "q",
        "a",
        "negative",
        sources=[
            {"document_id": " doc-a "},
            {"document_id": "doc-a"},
            {"document_id": "doc-b"},
            {"document_id": "   "},
            {"document_id": 42},
            "not-a-dict",
        ],
        comment=None,
    )

    assert sorted(graph.calls) == [
        ("doc-a", "negative", None),
        ("doc-b", "negative", None),
    ]


async def test_insert_accepts_a_one_shot_iterable_of_sources(install, monkeypatch):
    """Regression: a generator was exhausted by the row write, so the graph
    counters were silently skipped."""
    recorder = install()
    graph = _Graph()
    monkeypatch.setattr(fb, "graph_db", graph)

    sources = ({"document_id": d} for d in ("doc-a", "doc-b"))
    await fb.insert_feedback("q", "a", "positive", sources=sources)

    assert sorted(graph.calls) == [
        ("doc-a", "positive", None),
        ("doc-b", "positive", None),
    ]
    ((_sql, params),) = recorder.calls
    assert "doc-a" in params[4] and "doc-b" in params[4]  # serialized row


async def test_insert_survives_a_misconfigured_graph_timeout(install, monkeypatch):
    """The graph side-channel must never fail the feedback write itself."""
    recorder = install()
    graph = _Graph()
    monkeypatch.setattr(fb, "graph_db", graph)
    monkeypatch.setattr(
        fb, "_app_config", SimpleNamespace(graph_feedback_timeout="soon")
    )

    await fb.insert_feedback("q", "a", "positive", sources=[{"document_id": "d"}])

    assert len(recorder.calls) == 1
    assert graph.calls == []


async def test_insert_does_not_retry_a_deterministic_error(install, monkeypatch):
    recorder = install()
    recorder.execute_error = psycopg.errors.UniqueViolation("dup")
    graph = _Graph()
    monkeypatch.setattr(fb, "graph_db", graph)
    attempts: list[int] = []
    original = recorder.connection

    @asynccontextmanager
    async def counting():
        attempts.append(1)
        async with original() as conn:
            yield conn

    monkeypatch.setattr(fb, "get_async_connection", counting)

    with pytest.raises(psycopg.errors.UniqueViolation):
        await fb.insert_feedback("q", "a", "positive", sources=[{"document_id": "d"}])

    assert len(attempts) == 1
    assert graph.calls == []


# --------------------------------------------------------------------------- #
# get_feedbacks
# --------------------------------------------------------------------------- #


async def test_get_feedbacks_filters_and_limits_by_tenant(install):
    recorder = install()

    await fb.get_feedbacks("negative", limit=5)

    ((sql_text, params),) = recorder.calls
    assert "WHERE tenant_id = %s AND feedback = %s" in sql_text
    assert sql_text.endswith("ORDER BY timestamp DESC LIMIT %s")
    assert params == [TENANT, "negative", 5]
    assert recorder.cursor_kwargs == [{"row_factory": fb.dict_row}]


@pytest.mark.parametrize("limit", [None, 0, -3])
async def test_get_feedbacks_without_a_positive_limit_is_unbounded(install, limit):
    recorder = install()

    await fb.get_feedbacks(limit=limit)

    ((sql_text, params),) = recorder.calls
    assert "LIMIT" not in sql_text
    assert "feedback = %s" not in sql_text
    assert params == [TENANT]


async def test_get_feedbacks_shapes_rows_and_omits_empty_fields(install):
    when = datetime.datetime(2026, 5, 1, 12, 0, tzinfo=datetime.UTC)
    rows = [
        {
            "id": 1,
            "query": "q1",
            "answer": "a1",
            "feedback": "positive",
            "conversation_id": "c-1",
            "comment": "great",
            "sources": json.dumps([{"document_id": "d1", "title": " T "}]),
            "timestamp": when,
        },
        {
            "id": 2,
            "query": "q2",
            "answer": "a2",
            "feedback": "negative",
            "conversation_id": None,
            "comment": "",
            "sources": "not json",
            "timestamp": None,
        },
    ]
    install(lambda _q: rows)

    first, second = await fb.get_feedbacks()

    assert first["conversation_id"] == "c-1"
    assert first["comment"] == "great"
    assert first["sources"] == [{"document_id": "d1", "title": "T"}]
    assert datetime.datetime.fromisoformat(first["timestamp"]) == when
    assert second == {
        "id": 2,
        "query": "q2",
        "answer": "a2",
        "feedback": "negative",
        "timestamp": None,
    }


# --------------------------------------------------------------------------- #
# get_feedback_analytics
# --------------------------------------------------------------------------- #

_DAY = datetime.date(2026, 5, 1)
_TS = datetime.datetime(2026, 5, 1, 9, 30, tzinfo=datetime.UTC)


def _analytics_rows(sql_text: str) -> list[dict[str, Any]]:
    if "AND sources IS NOT NULL" in sql_text:
        return [
            {
                "feedback": "positive",
                "sources": '[{"document_id": "d1"}]',
                "timestamp": _TS,
            },
            {
                "feedback": "negative",
                "sources": '[{"document_id": "d1"}]',
                "timestamp": None,
            },
            {"feedback": "negative", "sources": '[{"path": "p.md"}]', "timestamp": _TS},
        ]
    if "HAVING COUNT(*) >= %s" in sql_text:  # active-learning candidates
        return [
            {"query": "empty", "total": 0, "positives": 0, "negatives": 0},
            {"query": "loved", "total": 10, "positives": 10, "negatives": 0},
            {
                "query": "hated",
                "total": 4,
                "positives": 1,
                "negatives": 3,
                "last_timestamp": _TS,
            },
        ]
    if "HAVING COUNT(*) > 0" in sql_text:  # top queries
        return [
            {"query": "q-top", "total": 4, "positives": 3, "negatives": 1},
            {"query": "q-none", "total": None, "positives": None, "negatives": None},
        ]
    if "GROUP BY day" in sql_text:
        return [
            {"day": _DAY, "total": 3, "positives": 2, "negatives": 1},
            {"day": "2026-05-02", "total": None, "positives": None, "negatives": None},
        ]
    if sql_text.startswith("SELECT COUNT(*) AS total"):
        return [{"total": 4, "positives": 1, "negatives": 3}]
    return [  # recent
        {
            "id": 7,
            "query": "q",
            "answer": "a",
            "feedback": "negative",
            "conversation_id": "c-7",
            "comment": "wrong",
            "sources": '[{"url": "https://x"}]',
            "timestamp": _TS,
        },
        {
            "id": 8,
            "query": "q2",
            "answer": "a2",
            "feedback": "positive",
            "conversation_id": None,
            "comment": None,
            "sources": None,
            "timestamp": None,
        },
    ]


async def test_analytics_aggregates_every_section(install, monkeypatch):
    monkeypatch.setattr(fb, "ACTIVE_LEARNING_MAX_POSITIVE_RATE", 0.6)
    install(_analytics_rows)

    result = await fb.get_feedback_analytics(days=7, recent_limit=5, top_limit=5)

    assert (result["total_feedbacks"], result["positives"], result["negatives"]) == (
        4,
        1,
        3,
    )
    assert (result["positive_rate"], result["negative_rate"]) == (0.25, 0.75)
    assert result["timeseries"] == [
        {"date": "2026-05-01", "total": 3, "positives": 2, "negatives": 1},
        {"date": "2026-05-02", "total": 0, "positives": 0, "negatives": 0},
    ]
    recent_full, recent_bare = result["recent"]
    assert recent_full["conversation_id"] == "c-7"
    assert recent_full["comment"] == "wrong"
    assert recent_full["sources"] == [{"url": "https://x"}]
    assert set(recent_bare) == {"id", "query", "answer", "feedback", "timestamp"}

    top, none = result["top_queries"]
    assert top["positive_rate"] == 0.75
    assert none["total"] == 0 and none["positive_rate"] == 0.0

    docs = {d["document_id"] or d["path"]: d for d in result["top_documents"]}
    assert docs["d1"]["total"] == 2
    assert (docs["d1"]["positive_rate"], docs["d1"]["negative_rate"]) == (0.5, 0.5)
    assert docs["p.md"]["negative_rate"] == 1.0
    assert result["top_documents"][0]["document_id"] == "d1"  # most cited first

    # Empty rows are skipped; a well-liked query with few negatives is not a
    # learning candidate; a poorly rated one is.
    (candidate,) = result["learning_candidates"]
    assert candidate["query"] == "hated"
    assert candidate["negative_rate"] == 0.75
    assert candidate["last_timestamp"] is not None
    assert result["window"]["days"] == 7


async def test_analytics_on_an_empty_window_reports_zero_rates(install):
    install()

    result = await fb.get_feedback_analytics()

    assert result["total_feedbacks"] == 0
    assert (result["positive_rate"], result["negative_rate"]) == (0.0, 0.0)
    assert result["timeseries"] == result["recent"] == result["top_documents"] == []
    assert result["window"]["days"] is None


async def test_analytics_always_bounds_the_scan_and_the_limits(install, monkeypatch):
    monkeypatch.setattr(fb, "ANALYTICS_DEFAULT_DAYS", 30)
    monkeypatch.setattr(fb, "ANALYTICS_DOC_SCAN_LIMIT", 0)
    recorder = install()
    before = datetime.datetime.now(fb.APP_TIMEZONE)

    result = await fb.get_feedback_analytics(days=0, recent_limit=0, top_limit=-1)

    assert len(recorder.calls) == 6
    for sql_text, params in recorder.calls:
        assert "WHERE tenant_id = %s AND timestamp >= %s" in sql_text
        assert params[0] == TENANT
        # days=0 is clamped to a one-day window, never an unbounded scan.
        assert before - params[1] == pytest.approx(
            datetime.timedelta(days=1), abs=datetime.timedelta(seconds=5)
        )
    limited = {
        sql_text: params[2:]
        for sql_text, params in recorder.calls
        if sql_text.rstrip().endswith("LIMIT %s")
    }
    # Non-positive limits are raised to 1 rather than passed to Postgres.
    assert all(all(v >= 1 for v in extra) for extra in limited.values())
    assert len(limited) == 4
    since = datetime.datetime.fromisoformat(result["window"]["since"])
    assert since.tzinfo is not None


async def test_analytics_holds_at_most_two_pool_connections(install):
    """Six unbounded fetches per dashboard hit would drain the pool."""
    recorder = install()

    await fb.get_feedback_analytics(days=1)

    assert len(recorder.calls) == 6
    assert recorder.peak == 2
