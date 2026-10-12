"""The durable-execution path survives a crash with the run it started.

One case per defect the durability audit found:

* the budget lived only in memory until the run finished, so a crash handed
  the resumed run full caps;
* every resume path re-entered with nothing but the tenant, so a ReAct run
  came back as Tree-of-Thoughts;
* ownership was guessed from silence, so a long call let a second worker run
  the same loop again;
* a stale writer silently overwrote newer state.

The cross-backend persistence contract lives in
``test_checkpoint_store_contract.py``; these drive the orchestrator.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

import baselith.core.orchestration.checkpoint_init as checkpoint_init
from baselith.core.orchestration.checkpoint import (
    STATUS_COMPLETED,
    STATUS_RUNNING,
    Checkpoint,
    CheckpointLeaseHeldError,
    InMemoryCheckpointStore,
)
from baselith.core.orchestration.checkpoint_inputs import (
    UNRESOLVED_TOOLS_KEY,
    capture_run_inputs,
    set_resume_tool_resolver,
)
from baselith.core.orchestration.limits import LoopBudget
from baselith.core.orchestration.recovery import resume_interrupted_runs
from baselith.core.reasoning.react import ToolDefinition

pytestmark = [pytest.mark.unit]


def _orchestrator(store: Any, handler: Any) -> Any:
    from baselith.core.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(checkpoint_store=store, default_intent="test_intent")
    orch.classify_intent_async = AsyncMock(return_value="test_intent")  # type: ignore[method-assign]
    orch._flow_handlers["test_intent"] = handler
    return orch


async def _lookup(q: str) -> str:
    return f"found {q}"


LOOKUP = ToolDefinition(
    name="lookup", fn=_lookup, description="look up", category="read_only"
)


@pytest.fixture(autouse=True)
def _no_resolver():
    set_resume_tool_resolver(None)
    yield
    set_resume_tool_resolver(None)


def _expire_lease(store: InMemoryCheckpointStore, run_id: str) -> None:
    """Simulate the owning worker dying: its lease runs out."""
    store._store[run_id]["lease_expires_at"] = time.time() - 1


# --------------------------------------------------------------------------- #
# Defect 1 — the budget is persisted while the run is active
# --------------------------------------------------------------------------- #


async def test_the_budget_is_on_disk_after_every_step_not_only_at_the_end():
    store = InMemoryCheckpointStore()
    mid_run: dict[str, Any] = {}

    class _Handler:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            budget = context["loop_budget"]
            budget.tick()
            budget.tick()
            budget.record_tokens(1234)

            async def _tool() -> str:
                return "ok"

            await context["checkpoint"].run_step("t", {}, _tool)
            # What a crash right now would leave behind.
            mid_run["checkpoint"] = await store.load(context["checkpoint"].run_id)
            return {"response": "done"}

    await _orchestrator(store, _Handler()).process("q", run_id="run-b")

    persisted = mid_run["checkpoint"].budget
    assert persisted["iterations"] == 2
    assert persisted["tokens"] == 1234


async def test_a_run_resumed_after_a_crash_continues_its_counters():
    crashed = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="run-c", query="q", intent="test_intent")
    await crashed.save(checkpoint)
    budget = LoopBudget()
    from baselith.core.orchestration.checkpoint import CheckpointManager

    manager = CheckpointManager(crashed, checkpoint, budget=budget)
    for _ in range(3):
        budget.tick()

        async def _tool() -> str:
            return "ok"

        await manager.run_step("t", {"n": budget.iterations}, _tool)
    # The process dies here: no complete(), no update_budget().

    seen: dict[str, Any] = {}

    class _Peek:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            seen.update(context["loop_budget"].snapshot().__dict__)
            return {"response": "ok"}

    await _orchestrator(crashed, _Peek()).process("q", run_id="run-c", resume=True)
    assert seen["iterations"] == 3


# --------------------------------------------------------------------------- #
# Defect 3 — every resume path restores the request context
# --------------------------------------------------------------------------- #


def test_capture_keeps_inputs_and_never_secrets():
    inputs = capture_run_inputs(
        {
            "strategy": "react",
            "react_tools": [LOOKUP],
            "history_text": "user: hi",
            "image_paths": ["/data/a.png"],
            "attachment_data": b"\x89PNG",
            "user_id": "u-1",
            "tenant_id": "acme",
            "api_key": "sk-live",
            "headers": {"Authorization": "Bearer x", "Accept": "json"},
            "loop_budget": LoopBudget(),
            "callback": lambda: None,
        }
    )

    assert inputs["strategy"] == "react"
    assert inputs["react_tools"] == {
        "__tool_refs__": [
            {"name": "lookup", "category": "read_only", "description": "look up"}
        ]
    }
    assert inputs["history_text"] == "user: hi"
    assert inputs["image_paths"] == ["/data/a.png"]
    assert inputs["user_id"] == "u-1"
    assert inputs["headers"] == {"Accept": "json"}
    for absent in ("api_key", "attachment_data", "tenant_id", "loop_budget"):
        assert absent not in inputs
    assert inputs["_dropped"] == {
        "attachment_data": "content",
        "api_key": "secret",
        "callback": "unserializable",
    }
    assert "sk-live" not in repr(inputs) and "Bearer" not in repr(inputs)


def test_capture_is_size_bounded():
    from baselith.core.orchestration.checkpoint_inputs import MAX_INPUT_VALUE_BYTES

    inputs = capture_run_inputs(
        {"history_text": "x" * (MAX_INPUT_VALUE_BYTES + 1), "strategy": "bfs"}
    )
    assert "history_text" not in inputs
    assert inputs["_dropped"] == {"history_text": "too_large"}
    assert inputs["strategy"] == "bfs"


async def _interrupted_react_run(store: InMemoryCheckpointStore) -> None:
    """A ReAct run that started, recorded its inputs, and lost its worker."""
    seen: dict[str, Any] = {}

    class _Dies:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            seen["run_id"] = context["checkpoint"].run_id
            raise asyncio.CancelledError  # stands in for the process dying

    context = {
        "strategy": "react",
        "react_tools": [LOOKUP],
        "history_text": "user: earlier",
        "user_id": "u-1",
        "api_key": "sk-live",
        "tenant_id": "acme",
    }
    from baselith.core.context import reset_tenant_context, set_tenant_context

    token = set_tenant_context("acme")
    try:
        with pytest.raises(asyncio.CancelledError):
            await _orchestrator(store, _Dies()).process(
                "find it", context, run_id="run-r"
            )
    finally:
        reset_tenant_context(token)
    # A dying process records nothing more: put the run back to running.
    store._store["run-r"]["status"] = STATUS_RUNNING
    store._store["run-r"]["error"] = None


async def test_the_recovery_sweep_resumes_with_the_original_inputs():
    store = InMemoryCheckpointStore()
    await _interrupted_react_run(store)
    set_resume_tool_resolver({"lookup": LOOKUP}.get)
    seen: dict[str, Any] = {}

    class _Peek:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            seen.update(context)
            return {"response": "ok"}

    report = await resume_interrupted_runs(_orchestrator(store, _Peek()), store)

    assert report.resumed == ["run-r"]
    assert seen["strategy"] == "react"
    assert seen["react_tools"] == [LOOKUP]
    assert seen["history_text"] == "user: earlier"
    assert seen["user_id"] == "u-1"
    assert seen["tenant_id"] == "acme"
    assert "api_key" not in seen


async def test_a_resume_without_its_tools_says_so_instead_of_switching_engine():
    from baselith.core.orchestration.handlers.reasoning import ReasoningHandler

    store = InMemoryCheckpointStore()
    await _interrupted_react_run(store)  # no resolver installed
    handler = ReasoningHandler()
    handler._tot_engine = AsyncMock()

    orch = _orchestrator(store, handler)
    result = await orch.process("find it", {}, run_id="run-r", resume=True)

    handler._tot_engine.solve.assert_not_awaited()
    assert result["error"] is True
    assert result["metadata"]["error"] == "resume_tools_unresolved"
    assert result["metadata"]["unresolved_tools"] == ["lookup"]


async def test_the_resuming_caller_wins_over_captured_inputs():
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(
        run_id="run-w", query="q", inputs={"strategy": "bfs", "k": 3}
    )
    await store.save(checkpoint)
    seen: dict[str, Any] = {}

    class _Peek:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            seen.update(context)
            return {"response": "ok"}

    await _orchestrator(store, _Peek()).process(
        "q", {"strategy": "mcts"}, run_id="run-w", resume=True
    )
    assert seen["strategy"] == "mcts"
    assert seen["k"] == 3
    assert UNRESOLVED_TOOLS_KEY not in seen


# --------------------------------------------------------------------------- #
# Defect 5 — a per-run lease, not 300 s of silence, decides ownership
# --------------------------------------------------------------------------- #


async def test_a_silent_run_with_a_live_lease_is_not_resumed():
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="busy", query="q", updated_at=time.time() - 3600)
    await store.save(checkpoint)
    await store.claim("busy", "worker-a", 600.0)
    # The save stamped updated_at; age it again so only the lease protects it.
    store._store["busy"]["updated_at"] = time.time() - 3600
    orch = AsyncMock()

    report = await resume_interrupted_runs(orch, store, min_idle_seconds=300.0)

    orch.process.assert_not_awaited()
    assert report.skipped == ["busy"]


async def test_an_expired_lease_is_resumed_without_waiting_for_silence():
    store = InMemoryCheckpointStore()
    await store.save(Checkpoint(run_id="dead", query="q"))
    await store.claim("dead", "worker-a", 30.0, now=time.time() - 60)
    orch = AsyncMock()
    orch.process = AsyncMock(return_value={"response": "ok"})

    report = await resume_interrupted_runs(orch, store, min_idle_seconds=300.0)

    assert report.resumed == ["dead"]


async def test_two_concurrent_resumes_execute_the_run_once():
    store = InMemoryCheckpointStore()
    await store.save(Checkpoint(run_id="race", query="q", intent="test_intent"))
    started = asyncio.Event()
    release = asyncio.Event()
    runs: list[str] = []

    class _Slow:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            runs.append(context["checkpoint"].owner)
            started.set()
            await release.wait()
            return {"response": "ok"}

    orch = _orchestrator(store, _Slow())
    first = asyncio.create_task(orch.process("q", run_id="race", resume=True))
    await started.wait()
    with pytest.raises(CheckpointLeaseHeldError):
        await orch.process("q", run_id="race", resume=True)
    release.set()
    assert (await first)["response"] == "ok"
    assert len(runs) == 1


async def test_the_heartbeat_keeps_a_long_call_owned_and_the_end_releases_it(
    monkeypatch,
):
    monkeypatch.setattr(checkpoint_init, "configured_lease_seconds", lambda: 0.3)
    store = InMemoryCheckpointStore()
    during: dict[str, Any] = {}

    class _LongCall:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            first_expiry = context["checkpoint"].checkpoint.lease_expires_at
            await asyncio.sleep(0.8)  # well past the original 0.3 s lease
            during["stored"] = await store.load(context["checkpoint"].run_id)
            during["first_expiry"] = first_expiry
            return {"response": "ok"}

    await _orchestrator(store, _LongCall()).process("q", run_id="long")

    stored = during["stored"]
    assert stored.owner is not None
    assert stored.lease_expires_at > during["first_expiry"]
    assert stored.lease_expires_at > time.time() - 0.3
    final = await store.load("long")
    assert final.status == STATUS_COMPLETED
    assert final.owner is None and final.lease_expires_at is None


# --------------------------------------------------------------------------- #
# Defects 4 + 5 — a worker that lost the run stops instead of clobbering it
# --------------------------------------------------------------------------- #


async def test_a_worker_that_lost_its_lease_stops_before_the_next_side_effect():
    store = InMemoryCheckpointStore()
    effects: list[str] = []

    class _Handler:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            manager = context["checkpoint"]

            async def _charge() -> str:
                effects.append("charge")
                return "charged"

            await manager.run_step("charge", {"n": 1}, _charge)
            # Meanwhile the lease ran out and another worker took the run.
            _expire_lease(store, manager.run_id)
            await store.claim(manager.run_id, "worker-b", 600.0)
            manager.checkpoint.lease_expires_at = 0.0  # force the fence check
            await manager.run_step("charge", {"n": 2}, _charge)
            return {"response": "done"}

    result = await _orchestrator(store, _Handler()).process("q", run_id="lost")

    assert effects == ["charge"]
    assert result["error"] is True and result["checkpoint_conflict"] is True
    stored = await store.load("lost")
    assert stored.owner == "worker-b"
    assert stored.status == STATUS_RUNNING  # not overwritten by the loser


async def test_a_fresh_run_cannot_reuse_an_existing_run_id():
    from baselith.core.orchestration.checkpoint import CheckpointConflictError

    store = InMemoryCheckpointStore()
    await store.save(Checkpoint(run_id="taken", query="original"))

    class _Never:
        async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("must not run")

    with pytest.raises(CheckpointConflictError):
        await _orchestrator(store, _Never()).process("other", run_id="taken")
    assert (await store.load("taken")).query == "original"
