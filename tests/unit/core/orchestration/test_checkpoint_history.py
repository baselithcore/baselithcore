"""Tests for versioned checkpoint snapshots (state history + time-travel)."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config.orchestration import OrchestrationConfig
from core.orchestration.checkpoint import (
    STATUS_RUNNING,
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
)
from core.orchestration.checkpoint_history import (
    fork_run,
    get_state,
    get_state_history,
)

pytestmark = [pytest.mark.contract]


class TestConfigFlags:
    def test_history_disabled_by_default(self):
        cfg = OrchestrationConfig()
        assert cfg.checkpoint_history_enabled is False

    def test_history_limit_default(self):
        cfg = OrchestrationConfig()
        assert cfg.checkpoint_history_limit == 200


# --------------------------------------------------------------------------- #
# In-memory store snapshots
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestInMemoryHistory:
    async def test_disabled_by_default_records_nothing(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        await store.save(cp)
        assert await store.list_snapshots("r1") == []

    async def test_enabled_appends_snapshot_per_version(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        cp = Checkpoint(run_id="r1", query="q")
        await store.save(cp)  # version 1
        cp.status = "completed"
        await store.save(cp)  # version 2
        history = await store.list_snapshots("r1")
        assert [s["version"] for s in history] == [1, 2]
        assert history[0]["status"] == STATUS_RUNNING
        assert history[1]["status"] == "completed"

    async def test_limit_keeps_newest(self):
        store = InMemoryCheckpointStore(history_enabled=True, history_limit=2)
        cp = Checkpoint(run_id="r1")
        for _ in range(4):
            await store.save(cp)
        assert [s["version"] for s in await store.list_snapshots("r1")] == [3, 4]

    async def test_load_snapshot_returns_state_at_version(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        cp = Checkpoint(run_id="r1", plugin_data={"k": 1})
        await store.save(cp)  # v1
        cp.plugin_data["k"] = 2
        await store.save(cp)  # v2
        v1 = await store.load_snapshot("r1", 1)
        assert v1 is not None and v1.plugin_data["k"] == 1
        assert await store.load_snapshot("r1", 99) is None

    async def test_delete_drops_history(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        cp = Checkpoint(run_id="r1")
        await store.save(cp)
        await store.delete("r1")
        assert await store.list_snapshots("r1") == []


# --------------------------------------------------------------------------- #
# History helpers (backend-agnostic)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestHistoryHelpers:
    async def test_helpers_tolerate_stores_without_history(self):
        class MinimalStore:
            async def save(self, checkpoint):
                pass

        store = MinimalStore()
        assert await get_state_history(store, "r1") == []
        assert await get_state(store, "r1", 1) is None
        assert await fork_run(store, "r1", 1) is None

    async def test_get_state_history_and_get_state(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        cp = Checkpoint(run_id="r1", query="q")
        await store.save(cp)
        history = await get_state_history(store, "r1")
        assert len(history) == 1 and history[0]["version"] == 1
        state = await get_state(store, "r1", 1)
        assert state is not None and state.query == "q"

    async def test_fork_unknown_version_returns_none(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        await store.save(Checkpoint(run_id="r1"))
        assert await fork_run(store, "r1", 42) is None

    async def test_fork_creates_fresh_running_run(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        cp = Checkpoint(run_id="r1", tenant_id="t1", query="q", intent="qa_docs")
        mgr = CheckpointManager(store, cp)

        async def step_a():
            return "A"

        await mgr.run_step("toolA", {"x": 1}, step_a)
        await mgr.complete("done")

        # Fork at the version that recorded step A (version 2: init save + step).
        history = await get_state_history(store, "r1")
        step_version = next(
            s["version"] for s in history if s["status"] == STATUS_RUNNING and s["step"] == 1
        )
        fork = await fork_run(store, "r1", step_version, new_run_id="fork-1")
        assert fork is not None and fork.run_id == "fork-1"

        loaded = await store.load("fork-1")
        assert loaded.status == STATUS_RUNNING
        assert loaded.answer is None and loaded.error is None
        assert loaded.pending_approval is None
        assert loaded.query == "q" and loaded.tenant_id == "t1"
        assert len(loaded.steps) == 1  # step A carried over

    async def test_forked_run_replays_without_reexecution(self):
        store = InMemoryCheckpointStore(history_enabled=True)
        cp = Checkpoint(run_id="r1")
        mgr = CheckpointManager(store, cp)
        calls = []

        async def step_a():
            calls.append("a")
            return "A"

        await mgr.run_step("toolA", {"x": 1}, step_a)
        history = await get_state_history(store, "r1")
        fork = await fork_run(store, "r1", history[-1]["version"], new_run_id="f1")

        forked = await store.load("f1")
        mgr2 = CheckpointManager(store, forked)
        out = await mgr2.run_step("toolA", {"x": 1}, step_a)
        assert out == "A"
        assert calls == ["a"]  # replayed, not re-executed

        async def step_b():
            calls.append("b")
            return "B"

        assert await mgr2.run_step("toolB", {}, step_b) == "B"
        assert calls == ["a", "b"]  # continues live past the fork point
        assert fork.run_id == "f1"


# --------------------------------------------------------------------------- #
# Postgres store history (mocked connection)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestPostgresHistory:
    def _cursor_ctx(self, cursor):
        @asynccontextmanager
        async def _ctx(*args, **kwargs):
            yield cursor

        return _ctx

    def _store(self, cursor, **kwargs):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        patcher = patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            self._cursor_ctx(cursor),
        )
        return PostgresCheckpointStore(**kwargs), patcher

    async def test_save_disabled_issues_only_upsert(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        store, patcher = self._store(cursor)
        with patcher:
            await store.save(Checkpoint(run_id="r1"))
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert len(sqls) == 1
        assert "agent_checkpoint_history" not in sqls[0]

    async def test_save_enabled_appends_history_row(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        store, patcher = self._store(cursor, history_enabled=True)
        with patcher:
            cp = Checkpoint(run_id="r1", tenant_id="t1")
            await store.save(cp)
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("INSERT INTO agent_checkpoint_history" in s for s in sqls)
        insert_call = next(
            c
            for c in cursor.execute.call_args_list
            if "INSERT INTO agent_checkpoint_history" in c.args[0]
        )
        params = insert_call.args[1]
        assert params[0] == "r1" and params[1] == cp.version

    async def test_save_enabled_trims_history(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        store, patcher = self._store(cursor, history_enabled=True, history_limit=5)
        with patcher:
            await store.save(Checkpoint(run_id="r1"))
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("DELETE FROM agent_checkpoint_history" in s for s in sqls)

    async def test_save_enabled_no_trim_when_unlimited(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        store, patcher = self._store(cursor, history_enabled=True, history_limit=0)
        with patcher:
            await store.save(Checkpoint(run_id="r1"))
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert not any("DELETE FROM agent_checkpoint_history" in s for s in sqls)

    async def test_save_step_snapshots_server_side(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.rowcount = 1
        store, patcher = self._store(cursor, history_enabled=True)
        with patcher:
            cp = Checkpoint(run_id="r1", status=STATUS_RUNNING, step=1, version=3)
            await store.save_step(
                cp,
                "0:t:abc",
                {"tool_name": "t", "result": "x"},
                {"cursor": 0, "tool": "t"},
            )
        calls = cursor.execute.call_args_list
        snapshot_calls = [
            c for c in calls if "INSERT INTO agent_checkpoint_history" in c.args[0]
        ]
        assert len(snapshot_calls) == 1
        # Server-side copy from the live row: no full-data param crosses the wire.
        assert "SELECT" in snapshot_calls[0].args[0]
        assert snapshot_calls[0].args[1] == ("r1",)

    async def test_save_step_disabled_keeps_legacy_statements(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.rowcount = 1
        store, patcher = self._store(cursor)
        with patcher:
            cp = Checkpoint(run_id="r1", version=3)
            await store.save_step(cp, "0:t:abc", {"result": 1}, {"cursor": 0})
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert not any("agent_checkpoint_history" in s for s in sqls)

    async def test_initialize_creates_history_table_when_enabled(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        store, patcher = self._store(cursor, history_enabled=True)
        with patcher:
            await store.initialize()
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("agent_checkpoint_history" in s for s in sqls)

    async def test_list_snapshots_parses_rows(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchall = AsyncMock(
            return_value=[
                {"version": 1, "status": "running", "step": 0, "updated_at": 1.0},
                {"version": 2, "status": "completed", "step": 1, "updated_at": 2.0},
            ]
        )
        store, patcher = self._store(cursor, history_enabled=True)
        with patcher:
            rows = await store.list_snapshots("r1")
        assert [r["version"] for r in rows] == [1, 2]

    async def test_load_snapshot_parses_data(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(
            return_value={"data": {"run_id": "r1", "query": "hi", "version": 2}}
        )
        store, patcher = self._store(cursor, history_enabled=True)
        with patcher:
            cp = await store.load_snapshot("r1", 2)
        assert cp is not None and cp.query == "hi" and cp.version == 2

    async def test_load_snapshot_missing_returns_none(self):
        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        store, patcher = self._store(cursor, history_enabled=True)
        with patcher:
            assert await store.load_snapshot("r1", 9) is None


# --------------------------------------------------------------------------- #
# Factory wiring
# --------------------------------------------------------------------------- #


class TestFactoryWiring:
    def test_factory_passes_history_flags_to_memory_store(self, monkeypatch):
        import core.config.orchestration as orch_config
        from core.orchestration.checkpoint_factory import (
            get_default_checkpoint_store,
            reset_default_checkpoint_store,
        )

        monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_ENABLED", "true")
        monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_BACKEND", "memory")
        monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_HISTORY_ENABLED", "true")
        monkeypatch.setenv("ORCHESTRATOR_CHECKPOINT_HISTORY_LIMIT", "7")
        monkeypatch.setattr(orch_config, "_orchestration_config", None)
        reset_default_checkpoint_store()
        try:
            store = get_default_checkpoint_store()
            assert isinstance(store, InMemoryCheckpointStore)
            assert store._history_enabled is True
            assert store._history_limit == 7
        finally:
            reset_default_checkpoint_store()
            monkeypatch.setattr(orch_config, "_orchestration_config", None)
