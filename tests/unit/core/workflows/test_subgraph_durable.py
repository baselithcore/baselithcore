"""A SUBGRAPH node interrupted half-way resumes from its own recorded nodes.

The nested workflow runs under the node visit's child scope of the run's
checkpoint, so every inner node is a recorded step: a crash inside the
subgraph re-executes only the inner nodes that never finished.
"""

from __future__ import annotations

from typing import Any

import pytest

from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.workflows.builder import WorkflowBuilder
from baselith.core.workflows.executor import ExecutionStatus, WorkflowExecutor
from baselith.core.workflows.versioning import WORKFLOW_VERSION_KEY

pytestmark = pytest.mark.unit


class _Death(BaseException):
    """The process dies mid-node: nothing in the executor catches it."""


class _Counters:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {"fetch": 0, "enrich": 0, "after": 0}
        self.die_in_enrich = True

    def fetch(self, value: Any) -> str:
        self.calls["fetch"] += 1
        return f"{value}+fetched"

    def enrich(self, value: Any) -> str:
        self.calls["enrich"] += 1
        if self.die_in_enrich:
            raise _Death("killed")
        return f"{value}+enriched"

    def after(self, value: Any) -> str:
        self.calls["after"] += 1
        return f"{value}+after"


def _graphs(counters: _Counters) -> Any:
    inner = (
        WorkflowBuilder("inner")
        .start()
        .tool("fetch", tool_id="fetch")
        .tool("enrich", tool_id="enrich")
        .end()
        .build()
    )
    outer = (
        WorkflowBuilder("outer")
        .start()
        .subgraph("nested", workflow=inner)
        .tool("after", tool_id="after")
        .end()
        .build()
    )
    executor = WorkflowExecutor(
        tools={
            "fetch": counters.fetch,
            "enrich": counters.enrich,
            "after": counters.after,
        }
    )
    return outer, executor


async def _manager(store: InMemoryCheckpointStore, run_id: str) -> CheckpointManager:
    loaded = await store.load(run_id)
    if loaded is None:
        loaded = Checkpoint(run_id=run_id, query="q")
        await store.save(loaded)
    return CheckpointManager(store, loaded)


async def test_a_crash_inside_a_subgraph_reruns_only_its_unfinished_nodes() -> None:
    store = InMemoryCheckpointStore()
    counters = _Counters()
    outer, executor = _graphs(counters)

    with pytest.raises(_Death):
        await executor.execute(outer, "in", checkpoint=await _manager(store, "wf-sub"))
    assert counters.calls == {"fetch": 1, "enrich": 1, "after": 0}

    counters.die_in_enrich = False
    result = await executor.execute(
        outer, "in", checkpoint=await _manager(store, "wf-sub")
    )

    assert result.status == ExecutionStatus.COMPLETED
    assert result.output == "in+fetched+enriched+after"
    # fetch finished before the crash: replayed from the subgraph's scope.
    assert counters.calls == {"fetch": 1, "enrich": 2, "after": 1}

    stored = await store.load("wf-sub")
    assert stored is not None
    pins = [key for key in stored.plugin_data if key.startswith(WORKFLOW_VERSION_KEY)]
    # The outer definition and the nested one, each under its own key.
    assert len(pins) == 2

    # A third pass replays everything, the whole subgraph included.
    again = await executor.execute(
        outer, "in", checkpoint=await _manager(store, "wf-sub")
    )
    assert again.output == "in+fetched+enriched+after"
    assert counters.calls == {"fetch": 1, "enrich": 2, "after": 1}


async def test_without_a_checkpoint_the_subgraph_runs_plainly() -> None:
    counters = _Counters()
    counters.die_in_enrich = False
    outer, executor = _graphs(counters)
    result = await executor.execute(outer, "in")
    assert result.output == "in+fetched+enriched+after"
    assert counters.calls == {"fetch": 1, "enrich": 1, "after": 1}
