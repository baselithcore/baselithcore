"""WorkflowRuntime: limits, history, timeouts and approval pauses."""

import asyncio

import pytest

from baselith.core.config.workflows import WorkflowsConfig
from baselith.core.orchestration.checkpoint import (
    ApprovalPrincipal,
    InMemoryCheckpointStore,
    record_approval_decision,
)
from baselith.core.workflows.builder import WorkflowBuilder, WorkflowDefinition
from baselith.core.workflows.catalog import WorkflowCatalog
from baselith.core.workflows.executor import (
    ExecutionStatus,
    WorkflowExecutor,
    WorkflowResult,
)
from baselith.core.workflows.runtime import (
    AWAITING_APPROVAL,
    COMPLETED,
    FAILED,
    TIMED_OUT,
    RuntimeBusyError,
    UnknownRunError,
    UnknownWorkflowError,
    WorkflowRuntime,
    contains_human_gate,
)

pytestmark = [pytest.mark.unit]


def _config(**overrides) -> WorkflowsConfig:
    values = {"WORKFLOWS_ENABLED": "true", **overrides}
    return WorkflowsConfig(**values)


def _greeter(workflow_id="greet") -> WorkflowDefinition:
    wf = WorkflowBuilder("g").start().template("t", "hi {{ input }}").end().build()
    wf.id = workflow_id
    return wf


def _runtime(*workflows, config=None, executor=None, store=None) -> WorkflowRuntime:
    catalog = WorkflowCatalog()
    for wf in workflows:
        catalog.register(wf)
    return WorkflowRuntime(
        catalog,
        config=config or _config(),
        executor_factory=(lambda: executor)
        if executor
        else (lambda: WorkflowExecutor()),
        checkpoint_store=store,
    )


async def test_run_returns_the_output_and_remembers_the_run():
    runtime = _runtime(_greeter())
    record = await runtime.run("greet", "Ada", source="api")
    assert record.status == COMPLETED and record.output == "hi Ada"
    assert record.finished_at is not None and record.error is None
    assert runtime.get_run(record.run_id) is record
    assert runtime.list_runs("greet") == [record]
    assert runtime.list_runs("other") == []


async def test_unknown_workflow():
    with pytest.raises(UnknownWorkflowError):
        await _runtime().run("nope")
    with pytest.raises(UnknownWorkflowError):
        _runtime().submit("nope")


async def test_node_failure_is_a_failed_run_not_an_exception():
    wf = WorkflowBuilder("f").start().template("t", "{{ nodes.ghost }}").end().build()
    wf.id = "bad"
    record = await _runtime(wf).run("bad", "x")
    assert record.status == FAILED and "unresolved" in (record.error or "")


async def test_unexpected_executor_error_is_contained():
    class Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("kaput")

    record = await _runtime(_greeter(), executor=Boom()).run("greet", "x")
    assert record.status == FAILED and "kaput" in (record.error or "")


async def test_timeout_marks_the_run_timed_out():
    class Slow:
        async def execute(self, *a, **k):
            await asyncio.sleep(5)

    runtime = _runtime(
        _greeter(),
        config=_config(WORKFLOWS_RUN_TIMEOUT_SECONDS="0.05"),
        executor=Slow(),
    )
    record = await runtime.run("greet", "x")
    assert record.status == TIMED_OUT and "exceeded" in (record.error or "")


async def test_concurrency_limit_refuses_instead_of_queueing():
    gate = asyncio.Event()

    class Held:
        async def execute(self, *a, **k):
            await gate.wait()
            return WorkflowResult(
                workflow_id="x", status=ExecutionStatus.COMPLETED, output="ok"
            )

    runtime = _runtime(
        _greeter(), config=_config(WORKFLOWS_MAX_CONCURRENT_RUNS="1"), executor=Held()
    )
    first = runtime.submit("greet", "x")
    with pytest.raises(RuntimeBusyError):
        runtime.submit("greet", "y")
    with pytest.raises(RuntimeBusyError):
        await runtime.run("greet", "y")
    gate.set()
    await asyncio.sleep(0.05)
    assert first.finished_at is not None and first.status == COMPLETED
    # The slot is free again.
    assert runtime.submit("greet", "z").status == "running"
    await runtime.aclose()


async def test_submit_runs_in_the_background():
    runtime = _runtime(_greeter())
    record = runtime.submit("greet", "Bo", source="trigger")
    assert record.status == "running" and record.source == "trigger"
    for _ in range(50):
        if record.finished_at:
            break
        await asyncio.sleep(0.01)
    assert record.status == COMPLETED and record.output == "hi Bo"


async def test_history_is_bounded_and_keeps_running_runs():
    runtime = _runtime(_greeter(), config=_config(WORKFLOWS_RUN_HISTORY_SIZE="3"))
    ids = [(await runtime.run("greet", str(i))).run_id for i in range(6)]
    assert runtime.get_run(ids[0]) is None
    assert runtime.get_run(ids[-1]) is not None
    assert len(runtime.list_runs()) == 3


class TestApprovalPause:
    @staticmethod
    def _gated(counter: dict) -> tuple[WorkflowDefinition, WorkflowExecutor]:
        def side_effect(value):
            counter["n"] += 1
            return f"did:{value}"

        wf = (
            WorkflowBuilder("gated")
            .start()
            .tool("act", tool_id="act")
            .human("review")
            .template("done", "finished {{ output }}")
            .end()
            .build()
        )
        wf.id = "gated"
        return wf, WorkflowExecutor(tools={"act": side_effect})

    async def test_pause_decide_resume_replays_finished_nodes(self):
        counter = {"n": 0}
        wf, executor = self._gated(counter)
        store = InMemoryCheckpointStore()
        runtime = _runtime(wf, executor=executor, store=store)

        paused = await runtime.run("gated", "job")
        assert paused.status == AWAITING_APPROVAL
        assert counter["n"] == 1
        checkpoint = await store.load(paused.run_id)
        assert checkpoint.status == "awaiting_approval"
        assert checkpoint.intent == "workflow:gated"

        assert await record_approval_decision(
            store,
            paused.run_id,
            True,
            approver=ApprovalPrincipal(id="alice", auth_method="test"),
        )
        resumed = await runtime.resume(paused.run_id)
        assert resumed.run_id == paused.run_id
        assert resumed.status == COMPLETED
        assert resumed.output == "finished did:job"
        assert counter["n"] == 1, "the finished node must replay, not run again"

    async def test_denied_approval_fails_the_resumed_run(self):
        counter = {"n": 0}
        wf, executor = self._gated(counter)
        store = InMemoryCheckpointStore()
        runtime = _runtime(wf, executor=executor, store=store)
        paused = await runtime.run("gated", "job")
        await record_approval_decision(
            store,
            paused.run_id,
            False,
            approver=ApprovalPrincipal(id="bob", auth_method="test"),
        )
        resumed = await runtime.resume(paused.run_id)
        assert resumed.status == FAILED and "denied" in (resumed.error or "")

    async def test_gate_without_a_checkpoint_store_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            "baselith.core.orchestration.checkpoint_factory.get_default_checkpoint_store",
            lambda: None,
        )
        counter = {"n": 0}
        wf, executor = self._gated(counter)
        record = await _runtime(wf, executor=executor).run("gated", "job")
        assert record.status == FAILED
        assert "checkpoint" in (record.error or "").lower()

    async def test_resume_of_an_unknown_or_foreign_run(self):
        store = InMemoryCheckpointStore()
        runtime = _runtime(_greeter(), store=store)
        with pytest.raises(UnknownRunError):
            await runtime.resume("missing")

    async def test_resume_after_the_workflow_left_the_catalog(self):
        counter = {"n": 0}
        wf, executor = self._gated(counter)
        store = InMemoryCheckpointStore()
        runtime = _runtime(wf, executor=executor, store=store)
        paused = await runtime.run("gated", "job")
        runtime.catalog.remove("gated")
        with pytest.raises(UnknownWorkflowError):
            await runtime.resume(paused.run_id)


def test_contains_human_gate_looks_inside_nested_workflows():
    gate = WorkflowBuilder("g").start().human("h").end().build()
    plain = _greeter()
    outer = (
        WorkflowBuilder("o")
        .start()
        .subgraph("s", workflow=gate.to_dict())
        .end()
        .build()
    )
    assert contains_human_gate(gate)
    assert not contains_human_gate(plain)
    assert contains_human_gate(outer)
    looped = WorkflowBuilder("l").start().iterate("i", gate).end().build()
    assert contains_human_gate(looped)


async def test_iteration_items_replay_after_an_approval_resume():
    """Finished items must not run again when the run resumes past a gate."""
    calls: list[int] = []

    def act(value):
        calls.append(value)
        return value * 10

    sub = WorkflowBuilder("item").start().tool("t", tool_id="act").end().build()
    wf = (
        WorkflowBuilder("batch")
        .start()
        .iterate("each", sub, items="{{ input }}")
        .human("review")
        .template("done", "{{ output | json }}")
        .end()
        .build()
    )
    wf.id = "batch"
    store = InMemoryCheckpointStore()
    runtime = _runtime(wf, executor=WorkflowExecutor(tools={"act": act}), store=store)

    paused = await runtime.run("batch", [1, 2, 3])
    assert paused.status == AWAITING_APPROVAL and calls == [1, 2, 3]
    await record_approval_decision(
        store,
        paused.run_id,
        True,
        approver=ApprovalPrincipal(id="alice", auth_method="test"),
    )
    resumed = await runtime.resume(paused.run_id)
    assert resumed.status == COMPLETED, resumed.error
    assert resumed.output == "[10, 20, 30]"
    assert calls == [1, 2, 3], "items already finished must replay, not run again"
