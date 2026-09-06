"""Integration test: the tool ledger deduplicates across concurrent callers.

Runs only against a **real** PostgreSQL, and only with the real-database opt-in::

    docker compose up -d postgres
    BASELITH_TEST_REAL_DB=1 python -m pytest tests/integration/test_tool_ledger_postgres.py

(``tests/conftest.py`` mocks psycopg globally for the fast unit run; without the
flag every case here skips.)

The unit tests assert what the SQL *says*. The claim that matters cannot be
asserted that way: that two replicas racing the same key resolve to exactly one
executor. That is a property of `INSERT ... ON CONFLICT` under real concurrency,
and only a real server can decide it — a mocked cursor will agree with whatever
the code does.

Each case therefore runs the ledger against the live table, with connections
opened directly rather than through the shared pool: these tests must not be
subject to the pool mocking the unit run installs.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

from core.orchestration.idempotency import derive_idempotency_key

pytestmark = [pytest.mark.integration]

TABLE = "tool_invocations"
POLICY = "tenant_isolation"
MODULE = "core.orchestration.idempotency_postgres"


def _conninfo() -> str:
    from core.config import get_storage_config

    config = get_storage_config()
    return (
        f"postgresql://{config.db_user}:{config.db_password.get_secret_value()}"
        f"@{config.db_host}:{config.db_port}/{config.db_name}"
    )


def _real_db_enabled() -> bool:
    """The opt-in that leaves the real psycopg in place.

    Checked separately from reachability, and it is not belt-and-braces:
    ``tests/conftest.py`` mocks ``psycopg.AsyncConnection`` — but **not**
    ``psycopg.connect`` — when the flag is unset. A probe using the sync API
    would therefore report a reachable server while the async connection this
    ledger opens is a ``MagicMock``, and the cases would fail rather than skip.
    """
    return os.environ.get("BASELITH_TEST_REAL_DB", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _pg_available() -> bool:
    """True only against a real PostgreSQL."""
    try:
        import psycopg

        with (
            psycopg.connect(_conninfo(), connect_timeout=3) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT 1")
            row = cur.fetchone()
        return bool(row and row[0] == 1)
    except Exception:
        return False


@pytest.fixture
def ledger_table() -> Iterator[str]:
    """The shipped table and policy, verbatim from migration 009.

    Created here rather than assumed: the test must be runnable against a
    database whose migrations have not been applied, and creating it from the
    migration's own DDL keeps the two honest — a column renamed there and not
    here fails the SQL, which is the point.
    """
    if not _real_db_enabled():
        pytest.skip("set BASELITH_TEST_REAL_DB=1 to run against a real Postgres")
    if not _pg_available():
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    run_id = f"itest-{uuid.uuid4().hex[:8]}"
    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                key TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                tool TEXT NOT NULL,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                status TEXT NOT NULL DEFAULT 'in_flight',
                result JSONB,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        cur.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
        cur.execute(
            f"CREATE POLICY {POLICY} ON {TABLE} "
            "USING (tenant_id = COALESCE(current_setting('app.tenant_id', true), 'default')) "
            "WITH CHECK (tenant_id = COALESCE(current_setting('app.tenant_id', true), 'default'))"
        )

    yield run_id

    with psycopg.connect(_conninfo(), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE run_id = %s", (run_id,))


def _real_cursor_factory() -> Any:
    """A ``get_async_cursor`` replacement backed by a real async connection.

    One connection per call, deliberately: a shared connection would serialise
    the very concurrency these tests exist to exercise.
    """
    import psycopg

    @contextlib.asynccontextmanager
    async def _cursor(*, row_factory: Any = None) -> AsyncIterator[Any]:
        conn = await psycopg.AsyncConnection.connect(_conninfo(), autocommit=True)
        try:
            kwargs = {"row_factory": row_factory} if row_factory is not None else {}
            async with conn.cursor(**kwargs) as cur:
                yield cur
        finally:
            await conn.close()

    return _cursor


@pytest.fixture
def ledger(monkeypatch, ledger_table: str) -> Any:
    """A ``PostgresToolLedger`` talking to the live table."""
    from core.orchestration.idempotency_postgres import PostgresToolLedger

    monkeypatch.setattr(f"{MODULE}.get_async_cursor", _real_cursor_factory())
    return PostgresToolLedger(tenant_id="default")


class TestConcurrentClaim:
    async def test_exactly_one_of_many_racing_callers_wins(self, ledger, ledger_table):
        """The property the whole ledger exists for, under real concurrency."""
        key = derive_idempotency_key(ledger_table, 0, "charge_card", {"amount": 10})

        results = await asyncio.gather(
            *(
                ledger.begin(key, run_id=ledger_table, tool="charge_card")
                for _ in range(8)
            )
        )

        winners = [outcome for outcome in results if outcome is None]
        assert len(winners) == 1, "the primary key must admit exactly one executor"
        assert all(
            outcome.status == "in_flight" for outcome in results if outcome is not None
        )

    async def test_a_completed_call_is_replayed_not_re_executed(
        self, ledger, ledger_table
    ):
        key = derive_idempotency_key(ledger_table, 1, "charge_card", {"amount": 10})
        assert await ledger.begin(key, run_id=ledger_table, tool="charge_card") is None
        await ledger.complete(key, {"receipt": "r-42"})

        held = await ledger.begin(key, run_id=ledger_table, tool="charge_card")
        assert held is not None
        assert held.is_replayable
        assert held.result == {"receipt": "r-42"}

    async def test_a_failed_call_is_re_claimable(self, ledger, ledger_table):
        """The effect did not land, so the retry must be allowed to run."""
        key = derive_idempotency_key(ledger_table, 2, "charge_card", {"amount": 10})
        await ledger.begin(key, run_id=ledger_table, tool="charge_card")
        await ledger.fail(key, "gateway down")

        assert await ledger.begin(key, run_id=ledger_table, tool="charge_card") is None
        recorded = await ledger.lookup(key)
        assert recorded is not None
        assert recorded.status == "in_flight"
        assert recorded.error is None

    async def test_only_one_of_many_retries_of_a_failed_call_proceeds(
        self, ledger, ledger_table
    ):
        """Re-claiming must not re-open the race it just closed."""
        key = derive_idempotency_key(ledger_table, 3, "charge_card", {"amount": 10})
        await ledger.begin(key, run_id=ledger_table, tool="charge_card")
        await ledger.fail(key, "gateway down")

        results = await asyncio.gather(
            *(
                ledger.begin(key, run_id=ledger_table, tool="charge_card")
                for _ in range(6)
            )
        )
        assert len([r for r in results if r is None]) == 1

    async def test_distinct_steps_do_not_collide(self, ledger, ledger_table):
        """One run legitimately calling a tool twice is two ledger entries."""
        first = derive_idempotency_key(ledger_table, 4, "charge_card", {"amount": 10})
        second = derive_idempotency_key(ledger_table, 5, "charge_card", {"amount": 10})
        assert first != second
        assert await ledger.begin(first, run_id=ledger_table, tool="charge") is None
        assert await ledger.begin(second, run_id=ledger_table, tool="charge") is None


class TestRoundTrip:
    async def test_an_unseen_key_has_no_outcome(self, ledger, ledger_table):
        assert await ledger.lookup(f"absent-{uuid.uuid4().hex}") is None

    async def test_a_jsonb_result_survives_the_round_trip(self, ledger, ledger_table):
        key = derive_idempotency_key(ledger_table, 6, "lookup", {})
        await ledger.begin(key, run_id=ledger_table, tool="lookup")
        payload = {"nested": {"items": [1, 2, 3]}, "ok": True}
        await ledger.complete(key, payload)

        recorded = await ledger.lookup(key)
        assert recorded is not None
        assert recorded.result == payload
        assert recorded.recorded_at > 0

    async def test_a_failure_records_its_reason(self, ledger, ledger_table):
        key = derive_idempotency_key(ledger_table, 7, "charge_card", {})
        await ledger.begin(key, run_id=ledger_table, tool="charge_card")
        await ledger.fail(key, "boom")

        recorded = await ledger.lookup(key)
        assert recorded is not None
        assert recorded.status == "failed"
        assert recorded.error == "boom"
        assert not recorded.is_replayable


class TestPurge:
    async def test_purge_spares_unresolved_and_failed_rows(self, ledger, ledger_table):
        """`in_flight` is an open question and `failed` licenses a retry."""
        completed = derive_idempotency_key(ledger_table, 8, "t", {})
        failed = derive_idempotency_key(ledger_table, 9, "t", {})
        in_flight = derive_idempotency_key(ledger_table, 10, "t", {})
        for key in (completed, failed, in_flight):
            await ledger.begin(key, run_id=ledger_table, tool="t")
        await ledger.complete(completed, "done")
        await ledger.fail(failed, "boom")

        # Everything written above is younger than the window, so nothing goes.
        assert await ledger.purge_completed_before(3600) == 0

        # A window of zero seconds makes the completed row eligible, alone.
        await ledger.purge_completed_before(0)
        assert await ledger.lookup(completed) is None
        assert await ledger.lookup(failed) is not None
        assert await ledger.lookup(in_flight) is not None


class TestTenantIsolation:
    async def test_rows_carry_the_ledgers_tenant(self, monkeypatch, ledger_table):
        """The RLS policy in migration 009 has nothing to filter on otherwise."""
        from core.orchestration.idempotency_postgres import PostgresToolLedger

        monkeypatch.setattr(f"{MODULE}.get_async_cursor", _real_cursor_factory())
        key = derive_idempotency_key(ledger_table, 11, "t", {})
        await PostgresToolLedger(tenant_id="acme").begin(
            key, run_id=ledger_table, tool="t"
        )

        import psycopg

        with (
            psycopg.connect(_conninfo(), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(f"SELECT tenant_id FROM {TABLE} WHERE key = %s", (key,))
            row = cur.fetchone()
        assert row is not None and row[0] == "acme"

    async def test_the_shipped_policy_is_installed_on_the_table(self, ledger_table):
        import psycopg

        with (
            psycopg.connect(_conninfo(), autocommit=True) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT polname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                "WHERE c.relname = %s",
                (TABLE,),
            )
            policies = {row[0] for row in cur.fetchall()}
            cur.execute(
                "SELECT relrowsecurity FROM pg_class WHERE relname = %s", (TABLE,)
            )
            enabled = cur.fetchone()
        assert POLICY in policies
        assert enabled is not None and enabled[0] is True
