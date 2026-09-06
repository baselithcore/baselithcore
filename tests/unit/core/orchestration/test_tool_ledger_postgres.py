"""Unit tests for the durable Postgres tool-call ledger.

The claim semantics are the point of this ledger, so the tests pin them at the
SQL level: what the statement says, and what the object does with the row it
gets back. The property these cannot decide — that two replicas racing one key
resolve to exactly one executor — is proved against a real server in
``tests/integration/test_tool_ledger_postgres.py`` (``BASELITH_TEST_REAL_DB=1``).
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.orchestration.idempotency_postgres import PostgresToolLedger

MODULE = "core.orchestration.idempotency_postgres"


def _cursor_ctx(cursor):
    @asynccontextmanager
    async def ctx(*args, **kwargs):
        yield cursor

    return ctx


def _cursor(fetchone=None, rowcount=0):
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=fetchone)
    cursor.rowcount = rowcount
    return cursor


class TestLookup:
    async def test_unseen_key_returns_none(self):
        cursor = _cursor(fetchone=None)
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            assert await PostgresToolLedger().lookup("k") is None

    async def test_completed_row_is_replayable(self):
        cursor = _cursor(
            fetchone={
                "status": "completed",
                "result": {"charged": True},
                "error": None,
                "recorded_at": 1_700_000_000.0,
            }
        )
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            outcome = await PostgresToolLedger().lookup("k")
        assert outcome is not None
        assert outcome.is_replayable
        assert outcome.result == {"charged": True}
        assert outcome.recorded_at == 1_700_000_000.0

    async def test_in_flight_row_is_not_replayable(self):
        cursor = _cursor(
            fetchone={
                "status": "in_flight",
                "result": None,
                "error": None,
                "recorded_at": 1.0,
            }
        )
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            outcome = await PostgresToolLedger().lookup("k")
        assert outcome is not None
        assert not outcome.is_replayable

    async def test_json_text_column_is_decoded(self):
        """A driver handing back text must not leak a JSON string to the caller."""
        cursor = _cursor(
            fetchone={
                "status": "completed",
                "result": '{"ok": 1}',
                "error": None,
                "recorded_at": 0.0,
            }
        )
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            outcome = await PostgresToolLedger().lookup("k")
        assert outcome is not None
        assert outcome.result == {"ok": 1}

    async def test_non_json_text_result_survives_as_text(self):
        cursor = _cursor(
            fetchone={
                "status": "completed",
                "result": "plain text",
                "error": None,
                "recorded_at": 0.0,
            }
        )
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            outcome = await PostgresToolLedger().lookup("k")
        assert outcome is not None
        assert outcome.result == "plain text"


class TestClaim:
    async def test_won_claim_returns_none(self):
        cursor = _cursor(fetchone=("k",))
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            claimed = await PostgresToolLedger("acme").begin(
                "k", run_id="run-1", tool="charge"
            )
        assert claimed is None
        sql, params = cursor.execute.call_args.args
        assert "INSERT INTO tool_invocations" in sql
        assert "ON CONFLICT (key) DO UPDATE" in sql
        # Only a failed row may be re-taken; anything else must lose the race.
        assert "WHERE tool_invocations.status = 'failed'" in sql
        assert params == ("k", "run-1", "charge", "acme")

    async def test_lost_claim_returns_the_winning_row(self):
        """No returned row means somebody else owns the call."""
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(
            side_effect=[
                None,  # the claim matched nothing
                {  # the follow-up lookup
                    "status": "completed",
                    "result": "already done",
                    "error": None,
                    "recorded_at": 5.0,
                },
            ]
        )
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            outcome = await PostgresToolLedger("acme").begin(
                "k", run_id="run-1", tool="charge"
            )
        assert outcome is not None
        assert outcome.is_replayable
        assert outcome.result == "already done"

    async def test_lost_claim_with_vanished_row_reports_in_flight(self):
        """Deleted between the two statements: never report a re-execution.

        Returning ``None`` here would tell the caller it owns an effectful
        call that another worker may already have made.
        """
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=[None, None])
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            outcome = await PostgresToolLedger("acme").begin(
                "k", run_id="run-1", tool="charge"
            )
        assert outcome is not None
        assert outcome.status == "in_flight"
        assert not outcome.is_replayable

    async def test_tenant_falls_back_to_the_ambient_context(self):
        cursor = _cursor(fetchone=("k",))
        with (
            patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)),
            patch(f"{MODULE}.get_current_tenant_id", return_value="from-context"),
        ):
            await PostgresToolLedger().begin("k", run_id="r", tool="t")
        assert cursor.execute.call_args.args[1][3] == "from-context"


class TestOutcomeRecording:
    async def test_complete_serialises_the_result(self):
        cursor = _cursor()
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            await PostgresToolLedger().complete("k", {"charged": True})
        sql, params = cursor.execute.call_args.args
        assert "status = 'completed'" in sql
        assert params[0] == '{"charged": true}'
        assert params[1] == "k"

    async def test_complete_falls_back_to_str_for_exotic_values(self):
        class Opaque:
            def __str__(self) -> str:
                return "opaque"

        cursor = _cursor()
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            await PostgresToolLedger().complete("k", Opaque())
        assert cursor.execute.call_args.args[1][0] == '"opaque"'

    async def test_fail_records_the_error_and_frees_the_key(self):
        cursor = _cursor()
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            await PostgresToolLedger().fail("k", "boom")
        sql, params = cursor.execute.call_args.args
        assert "status = 'failed'" in sql
        assert params == ("boom", "k")


class TestPurge:
    async def test_purge_only_touches_completed_rows(self):
        cursor = _cursor(rowcount=3)
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            deleted = await PostgresToolLedger().purge_completed_before(3600)
        sql, params = cursor.execute.call_args.args
        assert "DELETE FROM tool_invocations" in sql
        assert "status = 'completed'" in sql
        assert params == (3600,)
        assert deleted == 3

    async def test_negative_rowcount_is_clamped(self):
        """psycopg reports -1 when the count is unknown; never surface it."""
        cursor = _cursor(rowcount=-1)
        with patch(f"{MODULE}.get_async_cursor", _cursor_ctx(cursor)):
            assert await PostgresToolLedger().purge_completed_before(60) == 0


class TestSchemaOwnership:
    def test_module_runs_no_ddl(self):
        """Alembic owns ``tool_invocations``; this module must not create it."""
        from pathlib import Path

        source = Path("core/orchestration/idempotency_postgres.py").read_text(
            encoding="utf-8"
        )
        assert "CREATE TABLE" not in source

    def test_ledger_matches_the_protocol_signatures(self):
        """The two ledgers stay interchangeable.

        ``ToolLedger`` is not ``runtime_checkable``, so the substitutability
        that matters — same methods, same signatures — is asserted directly
        rather than through ``isinstance``, which would only check names.
        """
        import inspect

        from core.orchestration.idempotency import InMemoryToolLedger, ToolLedger

        for name in ("lookup", "begin", "complete", "fail"):
            expected = inspect.signature(getattr(ToolLedger, name))
            for implementation in (PostgresToolLedger, InMemoryToolLedger):
                assert inspect.signature(getattr(implementation, name)) == expected, (
                    f"{implementation.__name__}.{name} diverged from the protocol"
                )


@pytest.mark.parametrize("status", ["in_flight", "completed"])
async def test_claim_is_refused_for_unresolved_and_completed_rows(status):
    """Documented contract, asserted through the in-memory twin.

    The two ledgers must agree: only a ``failed`` row is re-claimable.
    """
    from core.orchestration.idempotency import InMemoryToolLedger

    ledger = InMemoryToolLedger()
    assert await ledger.begin("k", run_id="r", tool="t") is None
    if status == "completed":
        await ledger.complete("k", "done")
    held = await ledger.begin("k", run_id="r", tool="t")
    assert held is not None and held.status == status


async def test_failed_row_is_reclaimable_in_both_ledgers():
    from core.orchestration.idempotency import InMemoryToolLedger

    ledger = InMemoryToolLedger()
    await ledger.begin("k", run_id="r", tool="t")
    await ledger.fail("k", "boom")
    assert await ledger.begin("k", run_id="r", tool="t") is None
