"""Integration test: the Postgres online-evaluation store against a real server.

Runs only with the real-database opt-in::

    docker compose up -d postgres
    BASELITH_TEST_REAL_DB=1 python -m pytest tests/integration/test_online_eval_store_postgres.py

The table is created by running ``upgrade()`` of migration 013 itself, so a
column renamed there and not in the store fails here.
"""

from __future__ import annotations

import importlib.util
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.integration.test_tool_ledger_postgres import (
    _conninfo,
    _pg_available,
    _real_cursor_factory,
    _real_db_enabled,
)

pytestmark = [pytest.mark.integration]

MODULE = "baselith.core.evaluation.online_store_postgres"
MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "baselith"
    / "core"
    / "db"
    / "migrations"
    / "versions"
    / "013_online_eval_results.py"
)


class _Op:
    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def execute(self, sql: str) -> None:
        self._cur.execute(sql)


@pytest.fixture
def tenant() -> Iterator[str]:
    if not _real_db_enabled():
        pytest.skip("set BASELITH_TEST_REAL_DB=1 to run against a real Postgres")
    if not _pg_available():
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    spec = importlib.util.spec_from_file_location("mig_013", MIGRATION)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    tenant_id = f"itest-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        migration.op = _Op(cur)
        migration.upgrade()

    yield tenant_id

    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM online_eval_results WHERE tenant_id = %s", (tenant_id,)
        )


@pytest.fixture
def store(monkeypatch, tenant: str) -> Any:
    from baselith.core.evaluation.online_store_postgres import PostgresOnlineEvalStore

    monkeypatch.setattr(f"{MODULE}.get_async_cursor", _real_cursor_factory())
    return PostgresOnlineEvalStore()


async def test_round_trip_and_retention(store: Any, tenant: str) -> None:
    from baselith.core.evaluation.online_store import OnlineEvalRecord

    old = OnlineEvalRecord(
        intent="qa",
        source="checks",
        score=0.5,
        quality="needs_improvement",
        tenant_id=tenant,
        run_id="old",
        flags=("too_short",),
        created_at=1.0,
    )
    new = OnlineEvalRecord(
        intent="qa",
        source="judge",
        score=0.9,
        quality="excellent",
        tenant_id=tenant,
        run_id="new",
        aspects={"relevance": 0.9},
        signals={"duration_ms": 12.0},
    )
    await store.save(old)
    await store.save(new)

    rows = await store.recent(intent="qa", tenant_id=tenant)
    assert [r.run_id for r in rows] == ["new", "old"]
    assert rows[0].aspects == {"relevance": 0.9}
    assert rows[1].flags == ("too_short",)
    assert rows[0].query_text is None  # raw text is never stored by default

    assert await store.purge_before(2.0) >= 1
    assert [r.run_id for r in await store.recent(tenant_id=tenant)] == ["new"]
