"""Tests for deterministic loop-history compaction and streaming deadlines."""

import asyncio

import pytest

from core.orchestration.budget_context import activate_budget, deactivate_budget
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits
from core.reasoning.history import (
    compact_history,
    compact_messages,
    history_token_budget,
)
from core.services.llm._deadline import stream_within_deadline

LONG = "observation " * 400  # ~ hundreds of tokens per entry


# ---------------------------------------------------------------------------
# compact_history
# ---------------------------------------------------------------------------


def test_under_budget_is_untouched():
    entries = ["short one", "short two"]
    assert compact_history(entries, max_tokens=1000) == entries


def test_over_budget_compacts_oldest_keeps_recent():
    entries = [f"{i}: {LONG}" for i in range(8)]
    result = compact_history(entries, max_tokens=500, keep_recent=2)
    assert result[0].startswith("[compacted]")
    # The newest keep_recent entries are intact.
    assert result[-1] == entries[-1]
    assert result[-2] == entries[-2]
    assert len(result) == len(entries)


def test_zero_budget_disables_compaction():
    entries = [LONG, LONG]
    assert compact_history(entries, max_tokens=0) == entries


def test_compaction_is_idempotent():
    entries = [f"{i}: {LONG}" for i in range(6)]
    once = compact_history(entries, max_tokens=300, keep_recent=1)
    twice = compact_history(once, max_tokens=300, keep_recent=1)
    assert once == twice


def test_input_not_mutated():
    entries = [LONG, LONG, "tail"]
    snapshot = list(entries)
    compact_history(entries, max_tokens=10, keep_recent=1)
    assert entries == snapshot


def test_env_budget(monkeypatch):
    monkeypatch.setenv("BASELITH_REACT_HISTORY_MAX_TOKENS", "1234")
    assert history_token_budget() == 1234
    monkeypatch.setenv("BASELITH_REACT_HISTORY_MAX_TOKENS", "not-a-number")
    assert history_token_budget() == 8000


# ---------------------------------------------------------------------------
# compact_messages
# ---------------------------------------------------------------------------


def test_messages_system_and_task_never_compacted():
    messages = [
        {"role": "system", "content": LONG},
        {"role": "user", "content": "the task " + LONG},
        {"role": "assistant", "content": LONG},
        {"role": "user", "content": f"Observation: {LONG}"},
        {"role": "assistant", "content": "recent"},
        {"role": "user", "content": "recent obs"},
    ]
    result = compact_messages(messages, max_tokens=200, keep_recent=2)
    assert result[0]["content"] == messages[0]["content"]  # system intact
    assert result[1]["content"] == messages[1]["content"]  # task intact
    assert result[2]["content"].startswith("[compacted]")
    assert result[-1] == messages[-1]
    # Copies, not shared dicts: the caller's list is unchanged.
    assert messages[2]["content"] == LONG


# ---------------------------------------------------------------------------
# stream_within_deadline
# ---------------------------------------------------------------------------


async def _chunks(n=3, delay=0.0):
    for i in range(n):
        if delay:
            await asyncio.sleep(delay)
        yield f"chunk-{i}"


@pytest.mark.asyncio
async def test_stream_passthrough_without_budget():
    got = [c async for c in stream_within_deadline(_chunks())]
    assert got == ["chunk-0", "chunk-1", "chunk-2"]


@pytest.mark.asyncio
async def test_stream_within_deadline_completes():
    token = activate_budget(LoopBudget(limits=LoopLimits(max_seconds=5.0)))
    try:
        got = [c async for c in stream_within_deadline(_chunks())]
        assert got == ["chunk-0", "chunk-1", "chunk-2"]
    finally:
        deactivate_budget(token)


@pytest.mark.asyncio
async def test_stalled_stream_maps_to_budget_exceeded():
    budget = LoopBudget(limits=LoopLimits(max_seconds=0.05))
    token = activate_budget(budget)
    try:
        with pytest.raises(BudgetExceededError) as exc_info:
            async for _ in stream_within_deadline(_chunks(n=5, delay=0.2)):
                pass
        assert exc_info.value.reason == "max_seconds"
    finally:
        deactivate_budget(token)


# ---------------------------------------------------------------------------
# Budget-aware tool timeout in ReAct
# ---------------------------------------------------------------------------


def test_effective_tool_timeout_shrinks_to_budget():
    from core.reasoning.react import ReActAgent

    agent = ReActAgent(tool_timeout=120.0)
    budget = LoopBudget(limits=LoopLimits(max_seconds=10.0))
    token = activate_budget(budget)
    try:
        effective = agent._effective_tool_timeout()
        assert effective is not None and effective <= 10.0
    finally:
        deactivate_budget(token)
    # Outside an orchestrated request: static cap.
    assert agent._effective_tool_timeout() == 120.0


def test_effective_tool_timeout_none_without_budget_or_cap():
    from core.reasoning.react import ReActAgent

    assert ReActAgent()._effective_tool_timeout() is None


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
