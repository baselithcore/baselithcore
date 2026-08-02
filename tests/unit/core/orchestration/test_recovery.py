"""Tests for crash recovery of checkpointed runs."""

from __future__ import annotations

from unittest.mock import AsyncMock

from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    Checkpoint,
    InMemoryCheckpointStore,
)
from core.orchestration.recovery import resume_interrupted_runs


async def _seed(store, run_id, status, query="continue the work"):
    checkpoint = Checkpoint(run_id=run_id, query=query, status=status)
    if status == STATUS_AWAITING_APPROVAL:
        checkpoint.pending_approval = {"tool": "wipe", "category": "destructive"}
    await store.save(checkpoint)


class TestResumeInterruptedRuns:
    async def test_resumes_running_runs(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "r1", "running")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={"response": "ok"})

        report = await resume_interrupted_runs(orchestrator, store)

        assert report.resumed == ["r1"]
        orchestrator.process.assert_awaited_once_with(
            "continue the work", run_id="r1", resume=True
        )

    async def test_awaiting_approval_never_auto_resumed(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "r2", STATUS_AWAITING_APPROVAL)
        orchestrator = AsyncMock()

        report = await resume_interrupted_runs(orchestrator, store)

        assert report.skipped == ["r2"]
        orchestrator.process.assert_not_awaited()

    async def test_failed_resume_does_not_block_sweep(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "bad", "running")
        await _seed(store, "good", "running")
        orchestrator = AsyncMock()

        async def _process(query, run_id=None, resume=False):
            if run_id == "bad":
                raise RuntimeError("poisoned")
            return {"response": "ok"}

        orchestrator.process = AsyncMock(side_effect=_process)
        report = await resume_interrupted_runs(orchestrator, store)

        assert report.resumed == ["good"]
        assert "poisoned" in report.failed["bad"]

    async def test_max_runs_bounds_the_sweep(self):
        store = InMemoryCheckpointStore()
        for i in range(5):
            await _seed(store, f"r{i}", "running")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={})

        report = await resume_interrupted_runs(orchestrator, store, max_runs=2)

        assert len(report.resumed) == 2
