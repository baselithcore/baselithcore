"""Bounded in-memory checkpoint store + default-on durable checkpointing.

Checkpointing is now on by default (``ORCHESTRATOR_CHECKPOINT_ENABLED``),
which makes the in-memory backend the default store on deployments without
Postgres — so it must be bounded: an unbounded per-run dict would leak memory
for the process lifetime.
"""

from __future__ import annotations

import pytest

from core.orchestration.checkpoint import Checkpoint
from core.orchestration.checkpoint_memory import InMemoryCheckpointStore


def test_checkpoint_enabled_defaults_to_true(monkeypatch):
    monkeypatch.delenv("ORCHESTRATOR_CHECKPOINT_ENABLED", raising=False)
    from core.config.orchestration import OrchestrationConfig

    assert OrchestrationConfig().checkpoint_enabled is True


@pytest.mark.asyncio
async def test_evicts_oldest_completed_run_beyond_cap():
    store = InMemoryCheckpointStore(max_entries=2)
    for i in range(3):
        checkpoint = Checkpoint(run_id=f"run-{i}", status="completed")
        await store.save(checkpoint)

    assert await store.load("run-0") is None
    assert await store.load("run-1") is not None
    assert await store.load("run-2") is not None


@pytest.mark.asyncio
async def test_prefers_evicting_non_resumable_over_resumable():
    store = InMemoryCheckpointStore(max_entries=2)
    await store.save(Checkpoint(run_id="running-old", status="running"))
    await store.save(Checkpoint(run_id="done-old", status="completed"))
    await store.save(Checkpoint(run_id="done-new", status="completed"))

    # The completed run was evicted even though it is newer than the
    # resumable one: recovery must never lose a resumable run to a cap
    # while a finished one is still occupying a slot.
    assert await store.load("running-old") is not None
    assert await store.load("done-old") is None
    assert await store.load("done-new") is not None


@pytest.mark.asyncio
async def test_all_resumable_still_enforces_hard_cap():
    store = InMemoryCheckpointStore(max_entries=2)
    for i in range(3):
        await store.save(Checkpoint(run_id=f"running-{i}", status="running"))

    remaining = [
        rid
        for rid in ("running-0", "running-1", "running-2")
        if await store.load(rid) is not None
    ]
    assert len(remaining) == 2
    assert "running-0" not in remaining  # oldest evicted


@pytest.mark.asyncio
async def test_updating_existing_run_never_triggers_eviction():
    store = InMemoryCheckpointStore(max_entries=2)
    first = Checkpoint(run_id="run-a", status="running")
    second = Checkpoint(run_id="run-b", status="running")
    await store.save(first)
    await store.save(second)

    first.status = "completed"
    await store.save(first)  # update in place: still 2 entries, no eviction

    assert await store.load("run-a") is not None
    assert await store.load("run-b") is not None


@pytest.mark.asyncio
async def test_default_store_is_unbounded_for_backward_compat():
    store = InMemoryCheckpointStore()
    for i in range(5):
        await store.save(Checkpoint(run_id=f"run-{i}", status="completed"))
    for i in range(5):
        assert await store.load(f"run-{i}") is not None
