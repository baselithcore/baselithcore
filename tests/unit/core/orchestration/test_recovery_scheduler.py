"""The recovery sweeps must keep running, not fire once at boot.

``sweep_stale_runs`` existed but nothing called it: a run wedged *after*
startup stayed ``running`` forever, invisible to the liveness probe and to the
operator. And ``resume_interrupted_runs`` only ran once per process, so a run
interrupted by a transient failure waited for the next restart.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from core.orchestration.checkpoint import (
    STATUS_FAILED,
    Checkpoint,
    InMemoryCheckpointStore,
)
from core.orchestration.recovery import recovery_sweep_loop, run_recovery_cycle

pytestmark = [pytest.mark.unit]


class _Orchestrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process(self, query, context=None, run_id=None, resume=False):
        self.calls.append(run_id)
        return {"response": "ok"}


async def _seed_running(store, run_id, *, age_seconds=0.0):
    """Seed a ``running`` checkpoint whose last progress is ``age_seconds`` old.

    ``save`` restamps ``updated_at``, so the age is written straight into the
    in-memory store's row (same technique as ``test_stale_sweep``).
    """
    await store.save(Checkpoint(run_id=run_id, query="q", status="running"))
    store._store[run_id]["updated_at"] = time.time() - age_seconds


class TestRunRecoveryCycle:
    async def test_cycle_resumes_an_idle_run(self) -> None:
        store = InMemoryCheckpointStore()
        await _seed_running(store, "idle", age_seconds=900.0)
        orchestrator = _Orchestrator()

        resumed, stale = await run_recovery_cycle(
            orchestrator,
            store,
            stale_after_seconds=1800.0,
            resume_after_seconds=300.0,
        )
        assert resumed.resumed == ["idle"]
        assert stale.stale == []

    async def test_cycle_leaves_a_still_running_run_alone(self) -> None:
        """A periodic sweep must not re-enter a run that is *executing*.

        ``updated_at`` moves on every step save, so recent progress is the
        signal that some worker still owns this run; resuming it anyway ran
        the same agent loop twice, duplicating tool side effects and LLM
        spend."""
        store = InMemoryCheckpointStore()
        await _seed_running(store, "busy", age_seconds=5.0)
        orchestrator = _Orchestrator()

        resumed, stale = await run_recovery_cycle(
            orchestrator,
            store,
            stale_after_seconds=1800.0,
            resume_after_seconds=300.0,
        )
        assert resumed.resumed == []
        assert resumed.skipped == ["busy"]
        assert orchestrator.calls == []

    async def test_loop_heartbeat_also_counts_as_progress(self) -> None:
        store = InMemoryCheckpointStore()
        await _seed_running(store, "beating", age_seconds=900.0)
        # Old updated_at, but the per-attempt loop heartbeat is fresh.
        store._store["beating"]["plugin_data"] = {
            "loop_last_progress_at": time.time() - 2.0
        }
        orchestrator = _Orchestrator()

        resumed, _ = await run_recovery_cycle(
            orchestrator,
            store,
            stale_after_seconds=1800.0,
            resume_after_seconds=300.0,
        )
        assert resumed.resumed == []
        assert orchestrator.calls == []

    async def test_resume_after_seconds_defaults_to_the_setting(self) -> None:
        from core.config.orchestration import get_orchestration_config

        assert get_orchestration_config().recovery_resume_after_seconds == 300.0
        store = InMemoryCheckpointStore()
        await _seed_running(store, "busy", age_seconds=5.0)
        orchestrator = _Orchestrator()

        resumed, _ = await run_recovery_cycle(
            orchestrator, store, stale_after_seconds=1800.0
        )
        assert resumed.resumed == []

    async def test_direct_callers_keep_the_unguarded_contract(self) -> None:
        """``resume_interrupted_runs`` without an explicit idle threshold is
        still the boot-time sweep it always was."""
        from core.orchestration.recovery import resume_interrupted_runs

        store = InMemoryCheckpointStore()
        await _seed_running(store, "fresh", age_seconds=1.0)
        orchestrator = _Orchestrator()
        report = await resume_interrupted_runs(orchestrator, store)
        assert report.resumed == ["fresh"]

    async def test_cycle_fails_a_wedged_run(self) -> None:
        store = InMemoryCheckpointStore()
        await _seed_running(store, "wedged", age_seconds=5000.0)

        class _Stuck:
            async def process(self, *a, **k):
                raise RuntimeError("still wedged")

        resumed, stale = await run_recovery_cycle(
            _Stuck(), store, stale_after_seconds=100.0
        )
        assert "wedged" in resumed.failed
        assert stale.stale == ["wedged"]
        assert (await store.load("wedged")).status == STATUS_FAILED

    async def test_a_broken_resume_sweep_still_runs_the_stale_sweep(self) -> None:
        store = InMemoryCheckpointStore()
        await _seed_running(store, "wedged", age_seconds=5000.0)

        class _BrokenList:
            def __init__(self, inner):
                self._inner = inner
                self.calls = 0

            def __getattr__(self, name):
                return getattr(self._inner, name)

            async def list_resumable(self, tenant_id=None, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("store hiccup")
                return await self._inner.list_resumable(tenant_id, **kwargs)

        wrapped = _BrokenList(store)
        resumed, stale = await run_recovery_cycle(
            _Orchestrator(), wrapped, stale_after_seconds=100.0
        )
        assert resumed.resumed == []
        assert stale.stale == ["wedged"]


class TestRecoverySweepLoop:
    async def test_loop_runs_the_requested_number_of_cycles(self) -> None:
        store = InMemoryCheckpointStore()
        await _seed_running(store, "r1", age_seconds=900.0)
        orchestrator = _Orchestrator()

        await recovery_sweep_loop(
            orchestrator,
            store,
            interval_seconds=0.001,
            # High enough that the stale sweep leaves the run alone, so it is
            # still 'running' (and still idle) on the next cycle.
            stale_after_seconds=100_000.0,
            resume_after_seconds=300.0,
            max_cycles=3,
        )
        # The stub orchestrator never records progress, so every cycle
        # re-enters the run: three cycles, three resumes.
        assert orchestrator.calls == ["r1", "r1", "r1"]

    async def test_loop_keeps_going_after_a_failing_cycle(self, monkeypatch) -> None:
        seen: list[int] = []

        async def _boom(*args, **kwargs):
            seen.append(1)
            raise RuntimeError("sweep exploded")

        monkeypatch.setattr(
            "core.orchestration.recovery.run_recovery_cycle", _boom, raising=True
        )
        await recovery_sweep_loop(
            _Orchestrator(),
            InMemoryCheckpointStore(),
            interval_seconds=0.001,
            stale_after_seconds=100.0,
            max_cycles=3,
        )
        assert len(seen) == 3

    async def test_loop_is_cancellable(self) -> None:
        store = InMemoryCheckpointStore()
        task = asyncio.create_task(
            recovery_sweep_loop(
                _Orchestrator(),
                store,
                interval_seconds=30.0,
                stale_after_seconds=100.0,
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_loop_rejects_a_non_positive_interval(self) -> None:
        with pytest.raises(ValueError):
            await recovery_sweep_loop(
                _Orchestrator(),
                InMemoryCheckpointStore(),
                interval_seconds=0,
                stale_after_seconds=100.0,
                max_cycles=1,
            )
