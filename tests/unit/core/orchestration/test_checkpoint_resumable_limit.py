"""``list_resumable`` must always be a bounded query.

Its result feeds ``resume_interrupted_runs`` at startup: after a crash that
left a large backlog of ``running`` rows, an unbounded ``SELECT`` would stream
the whole table into one list before recovery even begins.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.orchestration.checkpoint import (
    DEFAULT_RESUMABLE_LIMIT,
    MAX_RESUMABLE_LIMIT,
    STATUS_AWAITING_APPROVAL,
    STATUS_RUNNING,
    Checkpoint,
    InMemoryCheckpointStore,
)


def _cursor_ctx(cursor):
    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield cursor

    return _ctx


@pytest.mark.asyncio
class TestPostgresResumableLimit:
    async def _execute(self, limit=None, tenant_id=None):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchall = AsyncMock(return_value=[])
        with patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            _cursor_ctx(cursor),
        ):
            store = PostgresCheckpointStore()
            await store.list_resumable(tenant_id, limit=limit)
        return cursor.execute.call_args.args

    async def test_default_query_carries_a_limit(self):
        sql, params = await self._execute()
        assert "LIMIT %s" in sql
        assert params[-1] == DEFAULT_RESUMABLE_LIMIT

    async def test_explicit_limit_is_used(self):
        _, params = await self._execute(limit=7)
        assert params[-1] == 7

    async def test_limit_is_clamped(self):
        _, params = await self._execute(limit=10 * MAX_RESUMABLE_LIMIT)
        assert params[-1] == MAX_RESUMABLE_LIMIT

        _, params = await self._execute(limit=0)
        assert params[-1] == 1

    async def test_running_runs_are_ordered_first(self):
        """Pending approvals sit resumable indefinitely; without the status
        preference a long approval queue would fill every page and starve
        crash recovery, which only re-enters ``running`` runs."""
        sql, params = await self._execute()
        assert "ORDER BY (status <> %s), updated_at ASC" in sql
        assert STATUS_RUNNING in params

    async def test_tenant_filter_still_applies_with_the_limit(self):
        sql, params = await self._execute(tenant_id="t1")
        assert "tenant_id = %s" in sql
        assert "t1" in params
        assert params[-1] == DEFAULT_RESUMABLE_LIMIT


@pytest.mark.asyncio
class TestInMemoryResumableLimit:
    async def _seed(self, store, count, status=STATUS_RUNNING):
        for i in range(count):
            await store.save(Checkpoint(run_id=f"r{i}", query="q", status=status))

    async def test_default_bound_matches_the_postgres_backend(self):
        store = InMemoryCheckpointStore()
        await self._seed(store, DEFAULT_RESUMABLE_LIMIT + 5)
        assert len(await store.list_resumable()) == DEFAULT_RESUMABLE_LIMIT

    async def test_explicit_limit_truncates(self):
        store = InMemoryCheckpointStore()
        await self._seed(store, 10)
        assert len(await store.list_resumable(limit=3)) == 3

    async def test_awaiting_approval_still_listed(self):
        store = InMemoryCheckpointStore()
        await self._seed(store, 2, status=STATUS_AWAITING_APPROVAL)
        assert len(await store.list_resumable()) == 2


@pytest.mark.asyncio
async def test_recovery_sweep_stays_bounded_by_the_page():
    """The startup sweep composes both bounds: one page, then ``max_runs``."""
    from core.orchestration.recovery import resume_interrupted_runs

    store = InMemoryCheckpointStore()
    for i in range(30):
        await store.save(Checkpoint(run_id=f"r{i}", query="q", status=STATUS_RUNNING))

    orchestrator = AsyncMock()
    orchestrator.process = AsyncMock(return_value={})
    report = await resume_interrupted_runs(orchestrator, store, max_runs=5)

    assert len(report.resumed) == 5
    assert orchestrator.process.await_count == 5
