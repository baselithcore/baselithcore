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
            "continue the work", context={}, run_id="r1", resume=True
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

        async def _process(query, context=None, run_id=None, resume=False):
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


class _FakeLock:
    """Minimal stand-in for core.resilience.distributed_lock.DistributedLock."""

    def __init__(self, acquirable: bool = True, fail: bool = False):
        self._acquirable = acquirable
        self._fail = fail
        self.held = False
        self.released = False

    async def acquire(self, *, blocking=True, timeout=None, retry_interval=0.1):
        if self._fail:
            raise RuntimeError("redis down")
        self.held = self._acquirable
        return self._acquirable

    async def release(self):
        self.held = False
        self.released = True


class TestRecoverySweepLock:
    """With >1 worker/replica every lifespan runs the sweep; the lock makes
    exactly one of them do the work instead of N duplicate agent resumes."""

    async def test_sweep_skipped_when_another_replica_holds_the_lock(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "r1", "running")
        orchestrator = AsyncMock()
        lock = _FakeLock(acquirable=False)

        report = await resume_interrupted_runs(orchestrator, store, lock=lock)

        assert report.resumed == []
        orchestrator.process.assert_not_awaited()
        assert lock.released is False

    async def test_sweep_runs_and_releases_when_lock_acquired(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "r1", "running")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={})
        lock = _FakeLock(acquirable=True)

        report = await resume_interrupted_runs(orchestrator, store, lock=lock)

        assert report.resumed == ["r1"]
        assert lock.released is True

    async def test_lock_error_fails_open_and_still_sweeps(self):
        """Recovery matters more than exclusion: an unreachable lock backend
        must not silently disable crash recovery."""
        store = InMemoryCheckpointStore()
        await _seed(store, "r1", "running")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={})
        lock = _FakeLock(fail=True)

        report = await resume_interrupted_runs(orchestrator, store, lock=lock)

        assert report.resumed == ["r1"]


class TestTenancyOnResume:
    """A resumed run must re-enter under its own tenant.

    ``process`` was called with no context, so the run inherited whatever
    ambient tenant the boot sweep happened to carry ("default") — the tenant
    isolation guard then either stamped the wrong tenant on the context or
    raised a mismatch, depending on the deployment.
    """

    async def test_checkpoint_tenant_is_passed_and_bound(self):
        from core.context import get_current_tenant_id

        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="t1", query="q", status="running")
        checkpoint.tenant_id = "acme"
        await store.save(checkpoint)

        seen: dict = {}

        class _Orchestrator:
            async def process(self, query, context=None, run_id=None, resume=False):
                seen["context"] = context
                seen["ambient"] = get_current_tenant_id()
                return {"response": "ok"}

        report = await resume_interrupted_runs(_Orchestrator(), store)

        assert report.resumed == ["t1"]
        assert seen["context"] == {"tenant_id": "acme"}
        assert seen["ambient"] == "acme"

    async def test_ambient_tenant_is_restored_after_the_run(self):
        from core.context import get_current_tenant_id

        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="t2", query="q", status="running")
        checkpoint.tenant_id = "acme"
        await store.save(checkpoint)

        before = get_current_tenant_id()
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={"response": "ok"})
        await resume_interrupted_runs(orchestrator, store)
        assert get_current_tenant_id() == before

    async def test_ambient_tenant_is_restored_after_a_failed_run(self):
        from core.context import get_current_tenant_id

        store = InMemoryCheckpointStore()
        checkpoint = Checkpoint(run_id="t3", query="q", status="running")
        checkpoint.tenant_id = "acme"
        await store.save(checkpoint)

        before = get_current_tenant_id()
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(side_effect=RuntimeError("poisoned"))
        report = await resume_interrupted_runs(orchestrator, store)
        assert "t3" in report.failed
        assert get_current_tenant_id() == before

    async def test_tenantless_checkpoint_passes_an_empty_context(self):
        store = InMemoryCheckpointStore()
        await _seed(store, "t4", "running")

        seen: dict = {}

        class _Orchestrator:
            async def process(self, query, context=None, run_id=None, resume=False):
                seen["context"] = context
                return {"response": "ok"}

        await resume_interrupted_runs(_Orchestrator(), store)
        assert seen["context"] == {}
