"""A resumed Tree-of-Thoughts search rebuilds its tree from the record.

Every generation and evaluation call is a checkpoint step, and the search is a
deterministic function of their results, so a search killed part-way and run
again with the same checkpoint must (a) not repeat any call the first attempt
finished and (b) end with exactly the answer an uninterrupted search gives.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

import pytest

import baselith.core.reasoning.tot.cache as tot_cache
from baselith.core.orchestration.checkpoint import Checkpoint, CheckpointManager
from baselith.core.orchestration.checkpoint_memory import InMemoryCheckpointStore
from baselith.core.reasoning.tot.engine import TreeOfThoughts


class _Death(BaseException):
    """The process dies during an LLM call."""


class _LLM:
    """Deterministic thoughts and scores derived from the prompt text."""

    def __init__(self, die_on_generation: int | None = None) -> None:
        self.calls: Counter[str] = Counter()
        self._die_on = die_on_generation

    async def generate_response(self, prompt: str, **_: Any) -> str:
        digest = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
        if "possible next steps" in prompt:
            self.calls["generate"] += 1
            if self.calls["generate"] == self._die_on:
                raise _Death("killed")
            return f"1. idea-{digest % 97}\n2. idea-{digest % 89}"
        self.calls["evaluate"] += 1
        return f"0.{digest % 9 + 1}"


@pytest.fixture(autouse=True)
def _no_thought_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shared score cache would hide evaluation calls between passes."""

    def _unavailable(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("no cache in this test")

    monkeypatch.setattr(tot_cache, "get_thought_cache", _unavailable)


async def _solve(llm: _LLM, checkpoint: Any = None) -> dict[str, Any]:
    return await TreeOfThoughts(llm_service=llm).solve(
        "plan a trip",
        k=2,
        max_steps=3,
        strategy="mcts",
        iterations=5,
        checkpoint=checkpoint,
    )


@pytest.mark.asyncio
async def test_resumed_mcts_repeats_no_finished_call_and_matches_baseline() -> None:
    baseline_llm = _LLM()
    baseline = await _solve(baseline_llm)
    total = sum(baseline_llm.calls.values())

    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="tot-run", query="plan a trip")
    await store.save(checkpoint)
    first = _LLM(die_on_generation=3)
    with pytest.raises(_Death):
        await _solve(first, CheckpointManager(store, checkpoint))
    finished = sum(first.calls.values()) - 1  # the killed call never returned

    loaded = await store.load("tot-run")
    assert loaded is not None
    second = _LLM()
    resumed = await _solve(second, CheckpointManager(store, loaded))

    assert sum(second.calls.values()) == total - finished
    assert resumed["solution"] == baseline["solution"]
    assert resumed["steps"] == baseline["steps"]


@pytest.mark.asyncio
async def test_a_completed_search_replays_without_any_llm_call() -> None:
    store = InMemoryCheckpointStore()
    checkpoint = Checkpoint(run_id="tot-done", query="plan a trip")
    await store.save(checkpoint)
    first = await _solve(_LLM(), CheckpointManager(store, checkpoint))

    loaded = await store.load("tot-done")
    assert loaded is not None
    replay_llm = _LLM()
    again = await _solve(replay_llm, CheckpointManager(store, loaded))
    assert sum(replay_llm.calls.values()) == 0
    assert again["solution"] == first["solution"]
