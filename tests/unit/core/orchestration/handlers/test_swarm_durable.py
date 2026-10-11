"""A resumed swarm run skips the decomposition and every finished sub-task.

The swarm used to be all-or-nothing under a checkpoint: a crash during the
synthesis re-ran the LLM decomposition (possibly into different sub-tasks) and
every sub-task. Each pass below counts the model calls by kind.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

import baselith.core.orchestration.handlers.swarm_handler as sh
from baselith.core.config.swarm import SwarmConfig
from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.orchestration.handlers.swarm_handler import SwarmHandler

_SUBTASKS = [
    {"description": "research facts", "capability": "web_search"},
    {"description": "analyse trends", "capability": "data_analysis"},
    {"description": "dig deeper", "capability": "data_analysis"},
]


class _Death(BaseException):
    """A process death: no ``except Exception`` fallback swallows it."""


class _LLM:
    """Counts calls by kind; can kill the 'process' on chosen ones."""

    def __init__(self, *, die_on: set[str] | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self._die_on = die_on or set()

    async def generate_response(
        self, prompt: str, model: Any = None, json: bool = False, **_: Any
    ) -> str:
        if json:
            kind = "decompose"
        elif prompt.startswith("Synthesize"):
            kind = "synthesize"
        else:
            kind = next(
                t["description"] for t in _SUBTASKS if t["description"] in prompt
            )
        self.calls[kind] += 1
        if kind in self._die_on:
            raise _Death(kind)
        return _dumps(_SUBTASKS) if kind == "decompose" else f"out:{kind}"


def _dumps(value: Any) -> str:
    return json.dumps(value)


@pytest.fixture(autouse=True)
def _no_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sh, "enforce_iteration", lambda ctx: None)

    async def _noop(ctx: Any, name: str) -> None:
        return None

    monkeypatch.setattr(sh, "enforce_tool_invocation", _noop)


def _handler(llm: _LLM) -> SwarmHandler:
    return SwarmHandler(llm_service=llm, colony_config=SwarmConfig())


async def _fresh(store: InMemoryCheckpointStore) -> CheckpointManager:
    checkpoint = Checkpoint(run_id="swarm-run", query="q")
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


async def _reloaded(store: InMemoryCheckpointStore) -> CheckpointManager:
    loaded = await store.load("swarm-run")
    assert loaded is not None
    return CheckpointManager(store, loaded)


@pytest.mark.asyncio
async def test_resume_after_synthesis_crash_replays_decomposition_and_subtasks() -> (
    None
):
    store = InMemoryCheckpointStore()
    first = _LLM(die_on={"synthesize"})
    with pytest.raises(_Death):
        await _handler(first).handle("topic", {"checkpoint": await _fresh(store)})
    assert first.calls["decompose"] == 1
    assert sum(first.calls.values()) == 5  # decompose + 3 subtasks + synthesis

    second = _LLM()
    result = await _handler(second).handle(
        "topic", {"checkpoint": await _reloaded(store)}
    )

    assert dict(second.calls) == {"synthesize": 1}
    assert sorted(r["result"] for r in result["sub_results"]) == [
        "out:analyse trends",
        "out:dig deeper",
        "out:research facts",
    ]
    assert result["response"] == "out:synthesize"


@pytest.mark.asyncio
async def test_a_failed_subtask_is_not_recorded_and_reruns_alone() -> None:
    store = InMemoryCheckpointStore()
    # The sub-task's death is captured by the gather as a failed result; the
    # synthesis death then ends the pass.
    first = _LLM(die_on={"dig deeper", "synthesize"})
    with pytest.raises(_Death):
        await _handler(first).handle("topic", {"checkpoint": await _fresh(store)})

    second = _LLM()
    result = await _handler(second).handle(
        "topic", {"checkpoint": await _reloaded(store)}
    )
    assert dict(second.calls) == {"dig deeper": 1, "synthesize": 1}
    assert all(r["success"] for r in result["sub_results"])


@pytest.mark.asyncio
async def test_without_a_checkpoint_every_call_runs() -> None:
    llm = _LLM()
    await _handler(llm).handle("topic", {})
    await _handler(llm).handle("topic", {})
    assert llm.calls["decompose"] == 2
    assert llm.calls["synthesize"] == 2
