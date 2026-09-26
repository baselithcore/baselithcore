"""Tests for the run-listing seam (`list_runs`) used by operator surfaces.

``list_resumable`` answers "what must crash recovery pick up"; ``list_runs``
answers "what has this deployment run lately", including completed and failed
runs — the read path a run explorer needs.
"""

import pytest

from core.orchestration.checkpoint import Checkpoint
from core.orchestration.checkpoint_history import list_runs
from core.orchestration.checkpoint_memory import (
    InMemoryCheckpointStore,
    summarize_run,
)

pytestmark = [pytest.mark.contract, pytest.mark.asyncio]


async def _store_with_runs() -> InMemoryCheckpointStore:
    store = InMemoryCheckpointStore()
    running = Checkpoint(run_id="a", tenant_id="t1", query="one")
    done = Checkpoint(run_id="b", tenant_id="t1", query="two", status="completed")
    other = Checkpoint(run_id="c", tenant_id="t2", query="three")
    for checkpoint in (running, done, other):
        await store.save(checkpoint)
    return store


async def test_lists_runs_in_any_state_newest_first() -> None:
    store = await _store_with_runs()
    rows = await list_runs(store)
    assert {r["run_id"] for r in rows} == {"a", "b", "c"}
    assert rows == sorted(rows, key=lambda r: r["updated_at"], reverse=True)


async def test_scopes_by_tenant_and_status() -> None:
    store = await _store_with_runs()
    assert {r["run_id"] for r in await list_runs(store, tenant_id="t1")} == {"a", "b"}
    assert [r["run_id"] for r in await list_runs(store, status="completed")] == ["b"]


async def test_limit_caps_the_result() -> None:
    store = await _store_with_runs()
    assert len(await list_runs(store, limit=2)) == 2


async def test_unset_tenant_belongs_to_the_default_one() -> None:
    """Matches the Postgres column default, so both backends filter alike."""
    store = InMemoryCheckpointStore()
    await store.save(Checkpoint(run_id="legacy", query="no tenant"))
    assert [r["run_id"] for r in await list_runs(store, tenant_id="default")] == [
        "legacy"
    ]


async def test_summary_omits_heavy_fields_but_reports_their_size() -> None:
    checkpoint = Checkpoint(
        run_id="a",
        query="q",
        trajectory=[{"step": 1}, {"step": 2}],
        steps={"k": {"tool_name": "search"}},
        pending_approval={"tool_name": "delete"},
    )
    summary = summarize_run(checkpoint.to_dict())
    assert summary["trajectory_length"] == 2
    assert summary["awaiting_approval"] is True
    assert "trajectory" not in summary and "steps" not in summary


async def test_store_without_list_runs_falls_back_to_resumable_ids() -> None:
    """Protocol-only stores still answer, using the resumable set."""

    class MinimalStore:
        def __init__(self) -> None:
            self._data: dict[str, Checkpoint] = {}

        async def save(self, checkpoint: Checkpoint) -> None:
            self._data[checkpoint.run_id] = checkpoint

        async def load(self, run_id: str) -> Checkpoint | None:
            return self._data.get(run_id)

        async def delete(self, run_id: str) -> None:
            self._data.pop(run_id, None)

        async def list_resumable(self, tenant_id: str | None = None) -> list[str]:
            return [
                rid
                for rid, c in self._data.items()
                if c.status == "running"
                and (tenant_id is None or c.tenant_id == tenant_id)
            ]

    store = MinimalStore()
    await store.save(Checkpoint(run_id="a", tenant_id="t1", query="one"))
    await store.save(
        Checkpoint(run_id="b", tenant_id="t1", query="two", status="completed")
    )

    rows = await list_runs(store)  # type: ignore[arg-type]
    assert [r["run_id"] for r in rows] == ["a"]  # only resumable ids are reachable


async def test_postgres_run_list_has_no_nullable_catch_all() -> None:
    """Filters are appended only when given — no ``%s IS NULL OR`` predicate.

    The catch-all form failed twice over: untyped it was a ``42P18`` (the
    placeholder in the ``IS NULL`` test has no type context), and typed it
    still defeated the tenant index once psycopg prepared the statement and
    Postgres picked a generic plan (EXPLAIN: seq scan over every tenant).
    """
    from core.orchestration.checkpoint_postgres import _run_list_query

    sql, params = _run_list_query(None, None, 50)
    assert "IS NULL" not in sql.upper()
    assert "WHERE" not in sql
    assert params == [50]

    sql, params = _run_list_query("t1", None, 10)
    assert "WHERE tenant_id = %s ORDER BY updated_at DESC LIMIT %s" in sql
    assert params == ["t1", 10]

    sql, params = _run_list_query("t1", "failed", 5)
    assert "WHERE tenant_id = %s AND status = %s" in sql
    assert params == ["t1", "failed", 5]


async def test_postgres_run_list_strips_heavy_keys_server_side() -> None:
    """Only the summary crosses the wire; the trajectory length is kept."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from core.orchestration.checkpoint_postgres import (
        PostgresCheckpointStore,
        _run_list_query,
    )

    sql, _ = _run_list_query(None, None, 1)
    for heavy in ("steps", "trajectory", "plugin_data", "answer"):
        assert f"- '{heavy}'" in sql

    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(
        return_value=[
            {
                "data": {"run_id": "r1", "status": "completed", "step": 3},
                "trajectory_length": 7,
            }
        ]
    )

    class _Ctx:
        async def __aenter__(self):
            return cursor

        async def __aexit__(self, *exc):
            return False

    store = PostgresCheckpointStore.__new__(PostgresCheckpointStore)
    with patch(
        "core.orchestration.checkpoint_postgres.get_async_cursor",
        lambda **_: _Ctx(),
    ):
        rows = await store.list_runs(tenant_id="t1", limit=10)
    assert rows[0]["run_id"] == "r1"
    assert rows[0]["trajectory_length"] == 7
    assert cursor.execute.await_args.args[1] == ["t1", 10]
