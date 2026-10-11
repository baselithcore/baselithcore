"""Entry points that retry reuse their run; a draining replica hands runs back.

* A chat request and every attempt of an RQ agent-run job used to mint a fresh
  run id, so a retry restarted from scratch and the idempotency ledger had
  nothing to match. A stable id now resumes the checkpoint: completed steps
  are counted and must not run again.
* A run the recovery sweep finishes on behalf of a dead job must reach the
  job's tracker and webhook.
* A shutdown cancellation used to fail in-flight runs for good; under drain it
  now leaves them resumable, and an ordinary cancel still fails them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from baselith.core.lifecycle import drain
from baselith.core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    Checkpoint,
    InMemoryCheckpointStore,
)
from baselith.core.orchestration.checkpoint_lease import YIELDED_KEY
from baselith.core.orchestration.recovery import resume_interrupted_runs
from baselith.core.orchestration.run_completion import (
    COMPLETION_NOTIFY_KEY,
    register_completion_notifier,
)
from baselith.core.orchestration.run_ids import (
    derive_run_id,
    process_with_stable_run_id,
)
from baselith.core.task_queue.jobs import agent_run as job_module

pytestmark = [pytest.mark.unit]


def _orchestrator(store: Any, handler: Any) -> Any:
    from baselith.core.orchestration.orchestrator import Orchestrator

    orch = Orchestrator(checkpoint_store=store, default_intent="test_intent")
    orch.classify_intent_async = AsyncMock(return_value="test_intent")  # type: ignore[method-assign]
    orch._flow_handlers["test_intent"] = handler
    return orch


class _TwoStepHandler:
    """Two recorded steps; the second fails while ``fail_second`` is set."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.fail_second = True

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        checkpoint = context["checkpoint"]

        async def first() -> str:
            self.executed.append("first")
            return "one"

        async def second() -> str:
            self.executed.append("second")
            if self.fail_second:
                raise RuntimeError("provider down")
            return "two"

        a = await checkpoint.run_step("first", {}, first)
        b = await checkpoint.run_step("second", {}, second)
        return {"response": f"{a}+{b}"}


# --------------------------------------------------------------------------- #
# Stable ids
# --------------------------------------------------------------------------- #


def test_derived_ids_are_stable_and_scoped_to_tenant_and_principal() -> None:
    base = derive_run_id("chat", "idempotency:k1", tenant_id="t1", principal="u1")
    assert base == derive_run_id(
        "chat", "idempotency:k1", tenant_id="t1", principal="u1"
    )
    assert base.startswith("chat-")
    assert base != derive_run_id(
        "chat", "idempotency:k1", tenant_id="t2", principal="u1"
    )
    assert base != derive_run_id(
        "chat", "idempotency:k1", tenant_id="t1", principal="u2"
    )


async def test_a_retry_under_the_same_id_resumes_and_skips_completed_steps() -> None:
    store = InMemoryCheckpointStore()
    handler = _TwoStepHandler()
    orch = _orchestrator(store, handler)

    failed = await process_with_stable_run_id(orch, "q", {}, "run-x")
    assert failed["error"] is True
    assert handler.executed == ["first", "second"]

    handler.fail_second = False
    retried = await process_with_stable_run_id(orch, "q", {}, "run-x")

    assert retried["response"] == "one+two"
    assert handler.executed == ["first", "second", "second"]  # first replayed
    stored = await store.load("run-x")
    assert stored is not None and stored.status == STATUS_COMPLETED


async def test_a_retry_of_a_completed_run_returns_its_answer_without_running() -> None:
    store = InMemoryCheckpointStore()
    handler = _TwoStepHandler()
    handler.fail_second = False
    orch = _orchestrator(store, handler)
    await process_with_stable_run_id(orch, "q", {}, "run-done")

    again = await process_with_stable_run_id(orch, "q", {}, "run-done")
    assert again == {
        "response": "one+two",
        "intent": "test_intent",
        "run_id": "run-done",
        "replayed": True,
    }
    assert handler.executed == ["first", "second"]


async def test_a_paused_run_is_reported_not_resumed() -> None:
    store = InMemoryCheckpointStore()
    await store.save(
        Checkpoint(
            run_id="run-p",
            query="q",
            status=STATUS_AWAITING_APPROVAL,
            pending_approval={"tool_name": "pay", "category": "destructive"},
        )
    )
    orch = SimpleNamespace(checkpoint_store=store, process=AsyncMock())
    result = await process_with_stable_run_id(orch, "q", {}, "run-p")
    assert result["awaiting_approval"] is True
    assert result["pending_approval"]["tool_name"] == "pay"
    orch.process.assert_not_awaited()


async def test_an_id_reused_for_another_query_never_touches_that_run() -> None:
    store = InMemoryCheckpointStore()
    await store.save(Checkpoint(run_id="run-q", query="original", status=STATUS_FAILED))
    orch = SimpleNamespace(checkpoint_store=store, process=AsyncMock(return_value={}))
    await process_with_stable_run_id(orch, "different", {}, "run-q")
    orch.process.assert_awaited_once_with("different", {})


# --------------------------------------------------------------------------- #
# RQ agent-run job
# --------------------------------------------------------------------------- #


class _Tracker:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def mark_started(self, job_id: str, message: str = "") -> None:
        self.events.append(("started", job_id))

    def mark_completed(
        self, job_id: str, message: str = "", result: Any = None
    ) -> None:
        self.events.append(("completed", job_id))

    def mark_failed(self, job_id: str, error: str) -> None:
        self.events.append(("failed", job_id))

    def mark_retrying(self, job_id: str, error: str, retries_left: int) -> None:
        self.events.append(("retrying", job_id))


class _Webhooks:
    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event: str, data: dict[str, Any], **_: Any) -> list[Any]:
        self.emitted.append((event, data))
        return []


def test_every_attempt_of_a_job_is_the_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker, webhooks = _Tracker(), _Webhooks()
    monkeypatch.setattr(job_module, "_get_tracker", lambda: tracker)
    monkeypatch.setattr(job_module, "_get_webhooks", lambda: webhooks)
    job = SimpleNamespace(id="job-7", retries_left=1)
    monkeypatch.setattr(job_module, "get_current_job", lambda: job)
    seen: list[tuple[str | None, dict[str, Any] | None]] = []

    async def chat(
        req: Any, run_id: str | None = None, extra_context: Any = None
    ) -> Any:
        seen.append((run_id, extra_context))
        if len(seen) == 1:
            raise RuntimeError("worker attempt 1 failed")
        return SimpleNamespace(answer="ok", metadata={})

    monkeypatch.setattr(job_module, "_handle_chat", chat)
    with pytest.raises(RuntimeError):
        job_module.run_agent_task("hello")
    job.retries_left = 0
    job_module.run_agent_task("hello")

    assert seen[0][0] == seen[1][0] == job_module.agent_job_run_id("job-7")
    assert seen[0][1] == {
        COMPLETION_NOTIFY_KEY: {"kind": "task_queue.agent_run", "task_id": "job-7"}
    }


async def test_a_sweep_resumed_job_run_updates_tracker_and_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker, webhooks = _Tracker(), _Webhooks()
    monkeypatch.setattr(job_module, "_get_tracker", lambda: tracker)
    monkeypatch.setattr(job_module, "_get_webhooks", lambda: webhooks)
    store = InMemoryCheckpointStore()
    handler = _TwoStepHandler()
    handler.fail_second = False
    orch = _orchestrator(store, handler)
    # The worker died mid-run: a running checkpoint, no lease, notifier set.
    await store.save(
        Checkpoint(
            run_id="agent-job-x",
            query="q",
            status=STATUS_RUNNING,
            inputs={
                COMPLETION_NOTIFY_KEY: {
                    "kind": "task_queue.agent_run",
                    "task_id": "job-9",
                }
            },
        )
    )

    report = await resume_interrupted_runs(orch, store)

    assert report.resumed == ["agent-job-x"]
    assert ("completed", "job-9") in tracker.events
    assert webhooks.emitted[0][0] == "agent.completed"
    assert webhooks.emitted[0][1]["answer"] == "one+two"


async def test_a_registered_notifier_kind_is_called_and_unknown_kinds_ignored() -> None:
    calls: list[dict[str, Any]] = []

    async def notifier(spec: dict[str, Any], result: dict[str, Any]) -> None:
        calls.append(result)

    register_completion_notifier("test.kind", notifier)
    from baselith.core.orchestration.run_completion import notify_resumed_completion

    cp = Checkpoint(run_id="r", inputs={COMPLETION_NOTIFY_KEY: {"kind": "test.kind"}})
    assert await notify_resumed_completion(cp, {"response": "x"}) is True
    unknown = Checkpoint(run_id="r", inputs={COMPLETION_NOTIFY_KEY: {"kind": "nope"}})
    assert await notify_resumed_completion(unknown, {"response": "x"}) is False
    assert calls == [{"response": "x"}]


# --------------------------------------------------------------------------- #
# Graceful shutdown
# --------------------------------------------------------------------------- #


class _BlockingHandler:
    """Records one step, then waits until cancelled."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.executed: list[str] = []
        self.block = True

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        async def step() -> str:
            self.executed.append("step")
            return "done"

        await context["checkpoint"].run_step("step", {}, step)
        self.started.set()
        if self.block:
            await asyncio.Event().wait()
        return {"response": "finished"}


@pytest.fixture
def _drain_reset() -> Any:
    drain._reset_for_tests()
    yield
    drain._reset_for_tests()


async def _start_and_cancel(orch: Any, handler: _BlockingHandler, run_id: str) -> None:
    task = asyncio.create_task(orch.process("q", {}, run_id=run_id))
    await handler.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_drain_cancellation_leaves_the_run_resumable(_drain_reset: Any) -> None:
    store = InMemoryCheckpointStore()
    handler = _BlockingHandler()
    orch = _orchestrator(store, handler)
    drain.mark_draining()

    await _start_and_cancel(orch, handler, "run-drain")

    stored = await store.load("run-drain")
    assert stored is not None
    assert stored.status == STATUS_RUNNING
    assert stored.plugin_data[YIELDED_KEY]["reason"] == "shutdown"
    assert stored.owner is None  # lease released for another replica

    # Another replica's sweep picks it up at once, despite the silence guard.
    drain._reset_for_tests()
    handler.block = False
    report = await resume_interrupted_runs(orch, store, min_idle_seconds=300)
    assert report.resumed == ["run-drain"]
    assert handler.executed == ["step"]  # the recorded step replayed
    done = await store.load("run-drain")
    assert done is not None and done.status == STATUS_COMPLETED
    assert YIELDED_KEY not in done.plugin_data


async def test_an_ordinary_cancellation_still_fails_the_run(_drain_reset: Any) -> None:
    store = InMemoryCheckpointStore()
    handler = _BlockingHandler()
    orch = _orchestrator(store, handler)

    await _start_and_cancel(orch, handler, "run-cancel")

    stored = await store.load("run-cancel")
    assert stored is not None
    assert stored.status == STATUS_FAILED
    assert stored.error == "cancelled"
