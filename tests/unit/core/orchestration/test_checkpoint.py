"""Tests for durable checkpointing / resume of the agent loop."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.orchestration.checkpoint import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
    step_key,
)

pytestmark = [pytest.mark.contract]


# --------------------------------------------------------------------------- #
# Model + key helper
# --------------------------------------------------------------------------- #


class TestModel:
    def test_to_from_dict_roundtrip(self):
        cp = Checkpoint(
            run_id="r1",
            tenant_id="t1",
            query="q",
            intent="qa_docs",
            step=2,
            trajectory=[{"cursor": 0, "tool": "x"}],
            steps={"0:x:abc": {"result": 1}},
        )
        rebuilt = Checkpoint.from_dict(cp.to_dict())
        assert rebuilt == cp

    def test_from_dict_ignores_unknown_keys(self):
        cp = Checkpoint.from_dict({"run_id": "r", "bogus": 123})
        assert cp.run_id == "r"

    def test_step_key_deterministic_and_arg_sensitive(self):
        assert step_key(0, "t", {"a": 1, "b": 2}) == step_key(0, "t", {"b": 2, "a": 1})
        assert step_key(0, "t", {"a": 1}) != step_key(0, "t", {"a": 2})
        assert step_key(0, "t", {}) != step_key(1, "t", {})


# --------------------------------------------------------------------------- #
# InMemory store
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestInMemoryStore:
    async def test_save_load_delete(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1", query="q")
        await store.save(cp)
        loaded = await store.load("r1")
        assert loaded is not None and loaded.query == "q"
        await store.delete("r1")
        assert await store.load("r1") is None

    async def test_load_isolates_from_stored_copy(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1", plugin_data={"k": 1})
        await store.save(cp)
        loaded = await store.load("r1")
        loaded.plugin_data["k"] = 999  # mutate the returned copy
        again = await store.load("r1")
        assert again.plugin_data["k"] == 1  # stored copy untouched

    async def test_list_resumable_filters_by_status_and_tenant(self):
        store = InMemoryCheckpointStore()
        await store.save(Checkpoint(run_id="a", tenant_id="t1", status=STATUS_RUNNING))
        await store.save(
            Checkpoint(run_id="b", tenant_id="t1", status=STATUS_COMPLETED)
        )
        await store.save(Checkpoint(run_id="c", tenant_id="t2", status=STATUS_RUNNING))
        assert set(await store.list_resumable()) == {"a", "c"}
        assert await store.list_resumable(tenant_id="t1") == ["a"]

    async def test_save_bumps_version(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        await store.save(cp)
        await store.save(cp)
        assert cp.version == 2


# --------------------------------------------------------------------------- #
# CheckpointManager idempotency / replay
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestManager:
    async def test_run_step_executes_and_records(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        mgr = CheckpointManager(store, cp)
        calls = []

        async def fn():
            calls.append(1)
            return "result-A"

        out = await mgr.run_step("toolA", {"x": 1}, fn)
        assert out == "result-A"
        assert calls == [1]
        assert cp.step == 1
        # persisted
        stored = await store.load("r1")
        assert len(stored.steps) == 1
        assert stored.trajectory[0]["tool"] == "toolA"

    async def test_replay_skips_execution_returns_stored(self):
        """Simulated crash + resume: recorded step must not re-execute."""
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        calls = []

        async def fn():
            calls.append(1)
            return "R"

        # First pass records step 0.
        mgr1 = CheckpointManager(store, cp)
        await mgr1.run_step("toolA", {"x": 1}, fn)
        assert calls == [1]

        # Resume: reload from store, new manager (cursor resets to 0).
        resumed = await store.load("r1")
        mgr2 = CheckpointManager(store, resumed)
        assert mgr2.resumed is True
        out = await mgr2.run_step("toolA", {"x": 1}, fn)
        assert out == "R"
        assert calls == [1]  # fn NOT called again — side effect not duplicated

    async def test_divergent_args_execute_fresh(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        calls = []

        async def fn_a():
            calls.append("a")
            return "A"

        async def fn_b():
            calls.append("b")
            return "B"

        mgr = CheckpointManager(store, cp)
        await mgr.run_step("toolA", {"x": 1}, fn_a)
        # Same cursor position would only be reached on replay; here a *different*
        # manager pass with different args at cursor 0 gets a distinct key.
        resumed = await store.load("r1")
        mgr2 = CheckpointManager(store, resumed)
        out = await mgr2.run_step("toolA", {"x": 2}, fn_b)  # different args
        assert out == "B"
        assert calls == ["a", "b"]

    async def test_complete_and_fail_set_status(self):
        store = InMemoryCheckpointStore()
        cp = Checkpoint(run_id="r1")
        mgr = CheckpointManager(store, cp)
        await mgr.complete("final answer")
        assert (await store.load("r1")).status == STATUS_COMPLETED
        assert (await store.load("r1")).answer == "final answer"

        mgr2 = CheckpointManager(store, Checkpoint(run_id="r2"))
        await mgr2.fail("boom")
        assert (await store.load("r2")).status == STATUS_FAILED
        assert (await store.load("r2")).error == "boom"


# --------------------------------------------------------------------------- #
# Orchestrator integration
# --------------------------------------------------------------------------- #


class _StepHandler:
    """Flow handler that runs two idempotent steps; step B fails on pass 1."""

    def __init__(self):
        self.a_calls = 0
        self.b_calls = 0
        self.fail_b = True

    async def handle(self, query, context):
        mgr = context["checkpoint"]

        async def step_a():
            self.a_calls += 1
            return "A"

        await mgr.run_step("toolA", {"q": query}, step_a)

        async def step_b():
            self.b_calls += 1
            if self.fail_b:
                raise RuntimeError("step B crashed")
            return "B"

        b = await mgr.run_step("toolB", {"q": query}, step_b)
        return {"response": f"done:{b}"}


def _orchestrator(store):
    from core.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(checkpoint_store=store, default_intent="test_intent")
    orch.classify_intent_async = AsyncMock(return_value="test_intent")  # type: ignore
    return orch


@pytest.mark.asyncio
class TestOrchestratorIntegration:
    async def test_checkpoint_created_and_completed(self):
        store = InMemoryCheckpointStore()
        orch = _orchestrator(store)
        handler = _StepHandler()
        handler.fail_b = False
        orch._flow_handlers["test_intent"] = handler

        result = await orch.process("hello", intent="test_intent", run_id="run-1")
        assert result["response"] == "done:B"
        cp = await store.load("run-1")
        assert cp.status == STATUS_COMPLETED
        assert cp.answer == "done:B"

    async def test_crash_then_resume_replays_without_reexec(self):
        store = InMemoryCheckpointStore()
        orch = _orchestrator(store)
        handler = _StepHandler()  # fail_b True → crashes mid-run
        orch._flow_handlers["test_intent"] = handler

        # Pass 1: step A records, step B crashes → run marked failed.
        res1 = await orch.process("hello", intent="test_intent", run_id="run-2")
        assert res1["error"] is True
        cp1 = await store.load("run-2")
        assert cp1.status == STATUS_FAILED
        assert handler.a_calls == 1 and handler.b_calls == 1

        # Pass 2: resume same run_id; step B now succeeds.
        handler.fail_b = False
        res2 = await orch.process(
            "hello", intent="test_intent", run_id="run-2", resume=True
        )
        assert res2["response"] == "done:B"
        # Step A replayed from the checkpoint — NOT re-executed.
        assert handler.a_calls == 1
        assert handler.b_calls == 2
        assert (await store.load("run-2")).status == STATUS_COMPLETED

    async def test_resume_restores_budget_counters(self):
        store = InMemoryCheckpointStore()
        # Seed a failed checkpoint with prior budget usage.
        seeded = Checkpoint(
            run_id="run-3",
            intent="test_intent",
            status=STATUS_FAILED,
            budget={"iterations": 7, "tool_calls": 3, "cost_usd": 0.2},
        )
        await store.save(seeded)

        orch = _orchestrator(store)
        captured = {}

        class _BudgetPeekHandler:
            async def handle(self, query, context):
                captured["budget"] = context["loop_budget"].snapshot().__dict__
                return {"response": "ok"}

        orch._flow_handlers["test_intent"] = _BudgetPeekHandler()
        await orch.process("hi", intent="test_intent", run_id="run-3", resume=True)
        assert captured["budget"]["iterations"] == 7
        assert captured["budget"]["tool_calls"] == 3
        assert captured["budget"]["cost_usd"] == 0.2

    async def test_no_store_means_no_checkpoint_context(self):
        from core.orchestration.orchestrator import Orchestrator

        orch = Orchestrator(default_intent="test_intent")  # no store
        orch.classify_intent_async = AsyncMock(return_value="test_intent")  # type: ignore

        seen = {}

        class _H:
            async def handle(self, query, context):
                seen["has_checkpoint"] = "checkpoint" in context
                return {"response": "ok"}

        orch._flow_handlers["test_intent"] = _H()
        await orch.process("hi", intent="test_intent")
        assert seen["has_checkpoint"] is False


# --------------------------------------------------------------------------- #
# Postgres store (mocked connection)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestPostgresStore:
    def _cursor_ctx(self, cursor):
        @asynccontextmanager
        async def _ctx(*args, **kwargs):
            yield cursor

        return _ctx

    async def test_save_issues_upsert(self):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor = MagicMock()
        cursor.execute = AsyncMock()
        with patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            self._cursor_ctx(cursor),
        ):
            store = PostgresCheckpointStore()
            cp = Checkpoint(run_id="r1", tenant_id="t1", status=STATUS_RUNNING)
            await store.save(cp)
        sql = cursor.execute.call_args.args[0]
        assert "INSERT INTO agent_checkpoints" in sql
        assert "ON CONFLICT (run_id) DO UPDATE" in sql
        params = cursor.execute.call_args.args[1]
        assert params[0] == "r1" and params[1] == "t1"

    async def test_load_parses_jsonb(self):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(
            return_value={"data": {"run_id": "r1", "query": "hi"}}
        )
        with patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            self._cursor_ctx(cursor),
        ):
            store = PostgresCheckpointStore()
            cp = await store.load("r1")
        assert cp is not None and cp.query == "hi"

    async def test_load_missing_returns_none(self):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None)
        with patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            self._cursor_ctx(cursor),
        ):
            store = PostgresCheckpointStore()
            assert await store.load("nope") is None


# --------------------------------------------------------------------------- #
# Incremental step persistence (save_step fast-path)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestSaveStepFastPath:
    def _cursor_ctx(self, cursor):
        @asynccontextmanager
        async def _ctx(*args, **kwargs):
            yield cursor

        return _ctx

    async def test_manager_prefers_save_step_when_available(self):
        """A store exposing save_step gets the incremental call, not save()."""
        store = MagicMock()
        store.save = AsyncMock()
        store.save_step = AsyncMock()
        cp = Checkpoint(run_id="r1")
        mgr = CheckpointManager(store, cp)

        async def tool():
            return "out"

        result = await mgr.run_step("t", {"a": 1}, tool)

        assert result == "out"
        store.save_step.assert_awaited_once()
        store.save.assert_not_awaited()
        # The in-memory checkpoint was mutated before persisting.
        args = store.save_step.await_args.args
        assert args[0] is cp
        assert args[1] in cp.steps
        assert cp.steps[args[1]]["result"] == "out"

    async def test_manager_falls_back_to_full_save(self):
        """Protocol-only stores (no save_step) keep working unchanged."""

        class MinimalStore:
            def __init__(self):
                self.saved = []

            async def save(self, checkpoint):
                self.saved.append(checkpoint.to_dict())

        store = MinimalStore()
        mgr = CheckpointManager(store, Checkpoint(run_id="r2"))

        async def tool():
            return 42

        assert await mgr.run_step("t", {}, tool) == 42
        assert len(store.saved) == 1

    async def test_postgres_save_step_patches_row_in_place(self):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.rowcount = 1
        with patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            self._cursor_ctx(cursor),
        ):
            store = PostgresCheckpointStore()
            cp = Checkpoint(run_id="r1", status=STATUS_RUNNING, step=1, version=3)
            await store.save_step(
                cp,
                "0:t:abc",
                {"tool_name": "t", "result": "x"},
                {"cursor": 0, "tool": "t"},
            )
        sql = cursor.execute.call_args.args[0]
        assert "UPDATE agent_checkpoints" in sql
        assert "jsonb_set" in sql
        assert "INSERT" not in sql  # only the new step crosses the wire
        # Bookkeeping stayed in lock-step with save().
        assert cp.version == 4

    async def test_postgres_save_step_falls_back_to_upsert_when_row_missing(self):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor = MagicMock()
        cursor.execute = AsyncMock()
        cursor.rowcount = 0  # no existing row: first persist of the run
        with patch(
            "core.orchestration.checkpoint_postgres.get_async_cursor",
            self._cursor_ctx(cursor),
        ):
            store = PostgresCheckpointStore()
            cp = Checkpoint(run_id="fresh", version=0)
            await store.save_step(cp, "0:t:abc", {"result": 1}, {"cursor": 0})
        sqls = [c.args[0] for c in cursor.execute.call_args_list]
        assert any("UPDATE agent_checkpoints" in s for s in sqls)
        assert any("INSERT INTO agent_checkpoints" in s for s in sqls)
        # Version bumped exactly once (by the full save fallback).
        assert cp.version == 1
