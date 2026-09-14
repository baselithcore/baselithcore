"""Cancellation must be recorded, then propagated.

``_process_with_budget`` caught bare ``Exception``, so ``CancelledError`` —
which is *not* an ``Exception`` in 3.8+ — fell through untouched: a client
disconnect or a shutdown left the checkpoint pinned at ``running`` forever,
and the stale sweep only noticed it half an hour later. A cancelled run is
known to be over the moment it is cancelled; say so on the checkpoint and
re-raise so the cancellation still unwinds the task.
"""

from __future__ import annotations

import asyncio

import pytest

from core.orchestration.checkpoint import (
    STATUS_FAILED,
    InMemoryCheckpointStore,
)
from core.orchestration.mixins.execution import ExecutionMixin

pytestmark = [pytest.mark.unit]


class _Handler:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def handle(self, query, context):
        raise self._exc


class _Orchestrator(ExecutionMixin):
    def __init__(self, handler, store) -> None:
        self.memory_manager = None
        self.human_intervention = None
        self.feedback_collector = None
        self.contract_validator = None
        self.checkpoint_store = store
        self._flow_handlers = {"qa": handler}
        self._stream_handlers = {}
        self._memory_write_tasks = set()
        self._memory_write_sem = None

    async def classify_intent_async(self, query: str) -> str:
        return "qa"


async def test_cancelled_run_is_marked_failed_and_reraised() -> None:
    store = InMemoryCheckpointStore()
    orchestrator = _Orchestrator(_Handler(asyncio.CancelledError()), store)

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.process("do it", {}, "qa", run_id="run-x")

    checkpoint = await store.load("run-x")
    assert checkpoint is not None
    assert checkpoint.status == STATUS_FAILED
    assert checkpoint.error == "cancelled"


async def test_cancellation_without_a_checkpoint_still_propagates() -> None:
    orchestrator = _Orchestrator(_Handler(asyncio.CancelledError()), None)
    with pytest.raises(asyncio.CancelledError):
        await orchestrator.process("do it", {}, "qa")


async def test_cancelling_the_task_that_runs_process_propagates() -> None:
    store = InMemoryCheckpointStore()

    class _Slow:
        async def handle(self, query, context):
            await asyncio.sleep(30)
            return {"response": "never"}

    orchestrator = _Orchestrator(_Slow(), store)
    task = asyncio.create_task(orchestrator.process("do it", {}, "qa", run_id="run-y"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    checkpoint = await store.load("run-y")
    assert checkpoint is not None
    assert checkpoint.status == STATUS_FAILED
    assert checkpoint.error == "cancelled"


async def test_ordinary_errors_still_return_a_structured_result() -> None:
    store = InMemoryCheckpointStore()
    orchestrator = _Orchestrator(_Handler(ValueError("boom")), store)
    result = await orchestrator.process("do it", {}, "qa", run_id="run-z")
    assert result["error"] is True
    assert "boom" in result["response"]
    checkpoint = await store.load("run-z")
    assert checkpoint.status == STATUS_FAILED
    assert "boom" in (checkpoint.error or "")


async def test_a_second_cancellation_does_not_lose_the_record() -> None:
    """Cancellation arrives in waves: a shutdown cancels the task, then its
    cleanup. An unshielded persist would be cancelled too and the run would
    stay 'running' forever — the exact state this path exists to prevent."""
    store = InMemoryCheckpointStore()

    class _CancelOnFirstAwait:
        """A store whose save yields once, giving a pending cancel a chance to
        land inside the persist."""

        def __init__(self, inner):
            self._inner = inner
            self.saves = 0

        async def save(self, checkpoint):
            self.saves += 1
            await asyncio.sleep(0)
            await self._inner.save(checkpoint)

        async def load(self, run_id):
            return await self._inner.load(run_id)

        async def delete(self, run_id):
            return await self._inner.delete(run_id)

        async def list_resumable(self, tenant_id=None, **kwargs):
            return await self._inner.list_resumable(tenant_id, **kwargs)

    wrapped = _CancelOnFirstAwait(store)
    orchestrator = _Orchestrator(_Handler(asyncio.CancelledError()), wrapped)

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.process("do it", {}, "qa", run_id="run-s")

    checkpoint = await store.load("run-s")
    assert checkpoint is not None
    assert checkpoint.status == STATUS_FAILED
    assert checkpoint.error == "cancelled"
