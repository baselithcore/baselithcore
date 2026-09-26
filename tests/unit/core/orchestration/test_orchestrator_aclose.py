"""Background memory writes must be drained, not dropped, on shutdown.

``schedule_memory_write`` parks each post-response write in
``_memory_write_tasks``; nothing ever awaited that set, so a shutdown lost the
last interactions or failed them mid-write once the pools closed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.orchestration.orchestrator import Orchestrator


class _SlowMemory:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.saved: list[str] = []
        self.cancelled = 0

    async def remember(self, content: str, metadata: dict[str, Any]) -> None:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        self.saved.append(content)


def _orchestrator(memory: _SlowMemory) -> Orchestrator:
    orch = Orchestrator(default_intent="qa_docs")
    orch.memory_manager = memory  # type: ignore[assignment]
    return orch


async def test_aclose_waits_for_pending_writes():
    memory = _SlowMemory(0.01)
    orch = _orchestrator(memory)
    orch._schedule_memory_write("q", "a", "qa_docs")

    await orch.aclose(timeout=2.0)

    assert memory.saved == ["User Query: q", "Agent Response: a"]
    assert not orch._memory_write_tasks


async def test_aclose_cancels_writes_past_the_timeout():
    memory = _SlowMemory(30.0)
    orch = _orchestrator(memory)
    orch._schedule_memory_write("q", "a", "qa_docs")

    await orch.aclose(timeout=0.01)

    assert memory.saved == []
    assert memory.cancelled == 2
    assert not orch._memory_write_tasks


async def test_aclose_is_idempotent_and_refuses_new_writes():
    memory = _SlowMemory(0.0)
    orch = _orchestrator(memory)

    await orch.aclose()
    await orch.aclose()
    orch._schedule_memory_write("late", "", "qa_docs")
    await asyncio.sleep(0)

    assert memory.saved == []
    assert not getattr(orch, "_memory_write_tasks", set())


async def test_aclose_without_memory_is_a_noop():
    orch = Orchestrator(default_intent="qa_docs")
    await orch.aclose()
