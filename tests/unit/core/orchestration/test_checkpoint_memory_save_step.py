"""Incremental ``save_step`` on the in-memory checkpoint store.

Without it, ``CheckpointManager.run_step`` fell back to a full ``save()`` per
tool step — deep-copying the whole accumulated state every time, O(n²) copies
over an n-step run (twice that with history on). Postgres already had the
fast path; ``auto`` resolves to the memory store whenever Postgres is off.
"""

from __future__ import annotations

from core.orchestration.checkpoint import Checkpoint, InMemoryCheckpointStore


def _checkpoint(run_id: str = "r1") -> Checkpoint:
    return Checkpoint(run_id=run_id, query="q", status="running")


async def test_save_step_appends_incrementally():
    store = InMemoryCheckpointStore()
    checkpoint = _checkpoint()
    await store.save(checkpoint)
    version_before = checkpoint.version

    checkpoint.steps["tool:0"] = {"result": "A"}
    checkpoint.trajectory.append({"tool": "tool", "cursor": 0})
    checkpoint.step = 1
    await store.save_step(
        checkpoint, "tool:0", {"result": "A"}, {"tool": "tool", "cursor": 0}
    )

    loaded = await store.load("r1")
    assert loaded is not None
    assert loaded.steps["tool:0"] == {"result": "A"}
    assert loaded.trajectory == [{"tool": "tool", "cursor": 0}]
    assert loaded.step == 1
    assert loaded.version == version_before + 1


async def test_save_step_on_missing_run_falls_back_to_full_save():
    store = InMemoryCheckpointStore()
    checkpoint = _checkpoint("fresh")
    checkpoint.steps["tool:0"] = {"result": "A"}
    await store.save_step(
        checkpoint, "tool:0", {"result": "A"}, {"tool": "tool", "cursor": 0}
    )
    loaded = await store.load("fresh")
    assert loaded is not None
    assert loaded.steps["tool:0"] == {"result": "A"}


async def test_save_step_deep_copies_the_entry():
    store = InMemoryCheckpointStore()
    checkpoint = _checkpoint()
    await store.save(checkpoint)
    entry = {"result": ["mutable"]}
    await store.save_step(checkpoint, "tool:0", entry, {"tool": "tool"})
    entry["result"].append("mutated-after-save")

    loaded = await store.load("r1")
    assert loaded is not None
    assert loaded.steps["tool:0"] == {"result": ["mutable"]}


async def test_save_step_records_history_snapshot():
    store = InMemoryCheckpointStore(history_enabled=True)
    checkpoint = _checkpoint()
    await store.save(checkpoint)
    await store.save_step(checkpoint, "tool:0", {"result": "A"}, {"tool": "tool"})

    snapshots = await store.list_snapshots("r1")
    assert len(snapshots) == 2  # initial save + the step
    assert snapshots[-1]["version"] == checkpoint.version
