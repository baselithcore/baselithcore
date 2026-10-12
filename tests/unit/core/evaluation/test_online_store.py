"""Online evaluation stores: in-memory round trip, retention, Postgres SQL."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import pytest

from baselith.core.evaluation.online_store import (
    MAX_STORED_TEXT,
    InMemoryOnlineEvalStore,
    OnlineEvalRecord,
    OnlineEvalStore,
    build_online_store,
    stored_text,
)


def _record(**kw: Any) -> OnlineEvalRecord:
    fields: dict[str, Any] = {
        "intent": "qa",
        "source": "checks",
        "score": 0.8,
        "quality": "good",
    }
    fields.update(kw)
    return OnlineEvalRecord(**fields)


async def test_memory_round_trip_newest_first() -> None:
    store = InMemoryOnlineEvalStore()
    assert isinstance(store, OnlineEvalStore)
    first = _record(run_id="r1", created_at=100.0)
    second = _record(run_id="r2", intent="other", tenant_id="t2", created_at=200.0)
    await store.save(first)
    await store.save(second)

    assert [r.run_id for r in await store.recent()] == ["r2", "r1"]
    assert [r.run_id for r in await store.recent(intent="qa")] == ["r1"]
    assert [r.run_id for r in await store.recent(tenant_id="t2")] == ["r2"]
    assert len(await store.recent(limit=1)) == 1


async def test_memory_store_is_bounded() -> None:
    store = InMemoryOnlineEvalStore(max_records=3)
    for i in range(5):
        await store.save(_record(run_id=f"r{i}"))
    assert len(store) == 3
    assert [r.run_id for r in await store.recent()] == ["r4", "r3", "r2"]


async def test_memory_retention_purges_old_records() -> None:
    store = InMemoryOnlineEvalStore()
    await store.save(_record(run_id="old", created_at=10.0))
    await store.save(_record(run_id="new", created_at=1000.0))
    assert await store.purge_before(500.0) == 1
    assert [r.run_id for r in await store.recent()] == ["new"]


def test_build_online_store() -> None:
    assert build_online_store("none") is None
    assert isinstance(
        build_online_store("memory", max_records=2), InMemoryOnlineEvalStore
    )
    with pytest.raises(ValueError):
        build_online_store("redis")


def test_stored_text_is_redacted_and_capped() -> None:
    text = (
        "mail jane.doe@example.com with api_key=sk-abcdef123456 now "  # gitleaks:allow (fake fixture)
        + "x" * 10_000
    )
    stored = stored_text(text)
    assert stored is not None
    assert "jane.doe@example.com" not in stored
    assert "sk-abcdef123456" not in stored
    assert len(stored) <= MAX_STORED_TEXT
    assert stored_text(None) is None


def test_record_to_dict_is_json_friendly() -> None:
    payload = _record(flags=("refusal",), aspects={"not_refusal": 0.0}).to_dict()
    assert json.loads(json.dumps(payload))["flags"] == ["refusal"]


# -- Postgres store, against a recording cursor -------------------------------


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.rows = rows or []
        self.rowcount = 0

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((sql, params))
        if sql.lstrip().startswith("DELETE"):
            self.rowcount = 2

    async def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


@pytest.fixture
def pg(monkeypatch):
    pytest.importorskip("psycopg")
    import baselith.core.evaluation.online_store_postgres as module

    cursor = _Cursor()
    tenants: list[str] = []

    @asynccontextmanager
    async def fake_cursor(**_kw: Any):
        from baselith.core.context import get_tenant_or_default

        tenants.append(get_tenant_or_default())
        yield cursor

    monkeypatch.setattr(module, "get_async_cursor", fake_cursor)
    return module.PostgresOnlineEvalStore(), cursor, tenants


async def test_postgres_save_binds_the_record_tenant(pg) -> None:
    store, cursor, tenants = pg
    await store.save(_record(tenant_id="acme", run_id="r1", flags=("empty",)))
    sql, params = cursor.calls[0]
    assert "INSERT INTO online_eval_results" in sql
    assert params[1] == "acme" and params[2] == "r1"
    assert json.loads(params[8]) == ["empty"]
    assert tenants == ["acme"]  # RLS WITH CHECK passes as the flow's tenant


async def test_postgres_purge_runs_as_system(pg) -> None:
    store, cursor, tenants = pg
    assert await store.purge_before(123.0) == 2
    assert cursor.calls[0] == (
        "DELETE FROM online_eval_results WHERE created_at < %s",
        (123.0,),
    )
    assert tenants == ["system"]


async def test_postgres_recent_filters_and_rehydrates(pg) -> None:
    store, cursor, _ = pg
    cursor.rows = [
        {
            "id": "x",
            "tenant_id": "acme",
            "run_id": "r1",
            "intent": "qa",
            "source": "judge",
            "score": 0.7,
            "quality": "acceptable",
            "aspects": '{"relevance": 0.7}',
            "flags": [],
            "signals": {},
            "query_text": None,
            "response_text": None,
            "created_at": 5.0,
        }
    ]
    records = await store.recent(intent="qa", tenant_id="acme", limit=5)
    sql, params = cursor.calls[0]
    assert "WHERE tenant_id = %s AND intent = %s" in sql
    assert params == ("acme", "qa", 5)
    assert records[0].aspects == {"relevance": 0.7}
    assert records[0].source == "judge"
