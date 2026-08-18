"""Tests for the run-listing seam (`list_runs`) used by operator surfaces.

``list_resumable`` answers "what must crash recovery pick up"; ``list_runs``
answers "what has this deployment run lately", including completed and failed
runs — the read path a run explorer needs.
"""

import pytest

from core.orchestration.checkpoint import Checkpoint
from core.orchestration.checkpoint_history import list_runs
from core.orchestration.checkpoint_memory import (
    InMemoryCheckpointStore,
    summarize_run,
)

pytestmark = [pytest.mark.contract, pytest.mark.asyncio]


async def _store_with_runs() -> InMemoryCheckpointStore:
    store = InMemoryCheckpointStore()
    running = Checkpoint(run_id="a", tenant_id="t1", query="one")
    done = Checkpoint(run_id="b", tenant_id="t1", query="two", status="completed")
    other = Checkpoint(run_id="c", tenant_id="t2", query="three")
    for checkpoint in (running, done, other):
        await store.save(checkpoint)
    return store


async def test_lists_runs_in_any_state_newest_first() -> None:
    store = await _store_with_runs()
    rows = await list_runs(store)
    assert {r["run_id"] for r in rows} == {"a", "b", "c"}
    assert rows == sorted(rows, key=lambda r: r["updated_at"], reverse=True)


async def test_scopes_by_tenant_and_status() -> None:
    store = await _store_with_runs()
    assert {r["run_id"] for r in await list_runs(store, tenant_id="t1")} == {"a", "b"}
    assert [r["run_id"] for r in await list_runs(store, status="completed")] == ["b"]


async def test_limit_caps_the_result() -> None:
    store = await _store_with_runs()
    assert len(await list_runs(store, limit=2)) == 2


async def test_unset_tenant_belongs_to_the_default_one() -> None:
    """Matches the Postgres column default, so both backends filter alike."""
    store = InMemoryCheckpointStore()
    await store.save(Checkpoint(run_id="legacy", query="no tenant"))
    assert [r["run_id"] for r in await list_runs(store, tenant_id="default")] == [
        "legacy"
    ]


async def test_summary_omits_heavy_fields_but_reports_their_size() -> None:
    checkpoint = Checkpoint(
        run_id="a",
        query="q",
        trajectory=[{"step": 1}, {"step": 2}],
        steps={"k": {"tool_name": "search"}},
        pending_approval={"tool_name": "delete"},
    )
    summary = summarize_run(checkpoint.to_dict())
    assert summary["trajectory_length"] == 2
    assert summary["awaiting_approval"] is True
    assert "trajectory" not in summary and "steps" not in summary


async def test_store_without_list_runs_falls_back_to_resumable_ids() -> None:
    """Protocol-only stores still answer, using the resumable set."""

    class MinimalStore:
        def __init__(self) -> None:
            self._data: dict[str, Checkpoint] = {}

        async def save(self, checkpoint: Checkpoint) -> None:
            self._data[checkpoint.run_id] = checkpoint

        async def load(self, run_id: str) -> Checkpoint | None:
            return self._data.get(run_id)

        async def delete(self, run_id: str) -> None:
            self._data.pop(run_id, None)

        async def list_resumable(self, tenant_id: str | None = None) -> list[str]:
            return [
                rid
                for rid, c in self._data.items()
                if c.status == "running"
                and (tenant_id is None or c.tenant_id == tenant_id)
            ]

    store = MinimalStore()
    await store.save(Checkpoint(run_id="a", tenant_id="t1", query="one"))
    await store.save(
        Checkpoint(run_id="b", tenant_id="t1", query="two", status="completed")
    )

    rows = await list_runs(store)  # type: ignore[arg-type]
    assert [r["run_id"] for r in rows] == ["a"]  # only resumable ids are reachable
