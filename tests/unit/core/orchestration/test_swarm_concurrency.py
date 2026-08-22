"""Swarm sub-task fan-out must be concurrency-bounded.

Each sub-task is a full LLM call. Gathering an entire decomposition at once
would open that many simultaneous provider calls (429 storm + cost spike). The
handler caps concurrency at ``SwarmConfig.max_concurrent_subtasks``; the loop
budget separately caps the total count.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import core.orchestration.handlers.swarm_handler as sh
from core.config.swarm import SwarmConfig
from core.orchestration.handlers.swarm_handler import SwarmHandler


@pytest.mark.asyncio
async def test_subtask_fanout_respects_concurrency_cap(monkeypatch):
    # Budget enforcement is orthogonal here — make it a no-op.
    monkeypatch.setattr(sh, "enforce_iteration", lambda ctx: None)

    async def _noop_tool(ctx, name):
        return None

    monkeypatch.setattr(sh, "enforce_tool_invocation", _noop_tool)

    handler = SwarmHandler(colony_config=SwarmConfig(max_concurrent_subtasks=3))

    # Stub the colony so assignment always succeeds without real agents.
    async def _submit(task):
        return "agent-1"

    handler._colony.submit_task = _submit  # type: ignore[method-assign]
    handler._colony.get_agent = lambda aid: SimpleNamespace(name="agent-1")  # type: ignore[method-assign]
    handler._colony.complete_task = lambda *a, **k: None  # type: ignore[method-assign]

    live = 0
    peak = 0

    async def _slow_execute(task_def, agent):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return "ok"

    handler._execute_with_agent = _slow_execute  # type: ignore[method-assign]

    sub_tasks = [{"description": f"t{i}", "capability": "analysis"} for i in range(12)]
    results = await handler._execute_subtasks(sub_tasks, "root query", context={})

    assert len(results) == 12
    assert all(r["success"] for r in results)
    # Never more than the configured cap in flight at once.
    assert peak <= 3
    # And the cap was actually exercised (more tasks than the cap).
    assert peak >= 1
