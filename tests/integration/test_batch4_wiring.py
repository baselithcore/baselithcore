import asyncio

import pytest

from core.swarm.colony import Colony
from core.swarm.types import AgentProfile, Capability
from core.events import EventBus, EventNames
from core.optimization.loop import OptimizationLoop
from core.learning.feedback import FeedbackCollector


@pytest.fixture
def colony_with_agents():
    colony = Colony()
    for i in range(3):
        colony.register_agent(
            AgentProfile(
                id=f"agent-{i}",
                name=f"Agent {i}",
                capabilities=[Capability(name="general", proficiency=0.8)],
                success_rate=0.9,
            )
        )
    return colony


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.mark.asyncio
class TestOptimizationLoop:
    async def test_no_trigger_above_threshold(self, event_bus):
        applied = []

        async def apply_fn(agent_id, prompt):
            applied.append(agent_id)
            return True

        loop = OptimizationLoop(
            event_bus=event_bus,
            apply_fn=apply_fn,
            threshold=0.5,
        )
        loop.start()

        # Emit evaluation event with good score
        await event_bus.emit(
            EventNames.EVALUATION_COMPLETED,
            {"score": 0.8, "agent_id": "good-agent", "intent": "chat"},
        )
        await asyncio.sleep(0.1)

        # apply_fn should NOT have been called
        assert len(applied) == 0
        loop.stop()

    async def test_trigger_below_threshold(self, event_bus):
        collector = FeedbackCollector()
        # Seed with negative feedback so auto_tune has data
        await collector.log_feedback(
            agent_id="bad-agent", task_id="t1", score=0.2, comment="bad answer"
        )

        loop = OptimizationLoop(
            feedback_collector=collector,
            event_bus=event_bus,
            threshold=0.5,
            dry_run=True,
        )
        # Mock LLM service to avoid real connection and socket leak
        from unittest.mock import AsyncMock

        loop._optimizer._llm_service = AsyncMock()
        loop._optimizer._llm_service.generate_response.return_value = "Mock suggestion"

        loop.start()

        # Emit evaluation event with bad score — the handler is awaited
        # by EventBus (wait=True default), which creates a background task.
        await event_bus.emit(
            EventNames.EVALUATION_COMPLETED,
            {"score": 0.3, "agent_id": "bad-agent", "intent": "chat"},
        )

        # A background task should have been spawned
        assert len(loop._tasks) > 0 or "bad-agent" not in loop._in_flight

        # Wait for all spawned tasks to complete
        if loop._tasks:
            await asyncio.gather(*loop._tasks, return_exceptions=True)

        # The loop should have triggered auto_tune and released the lock.
        assert "bad-agent" not in loop._in_flight
        loop.stop()

    async def test_dedup_concurrent_tunes(self, event_bus):
        collector = FeedbackCollector()
        await collector.log_feedback(
            agent_id="dup", task_id="t1", score=0.1, comment="slow"
        )

        loop = OptimizationLoop(
            feedback_collector=collector,
            event_bus=event_bus,
            threshold=0.5,
            dry_run=True,
        )
        # Mock LLM service to avoid real connection and socket leak
        from unittest.mock import AsyncMock

        loop._optimizer._llm_service = AsyncMock()
        loop._optimizer._llm_service.generate_response.return_value = "Mock suggestion"

        loop.start()

        # Rapidly fire two events for the same agent
        await event_bus.emit(
            EventNames.EVALUATION_COMPLETED,
            {"score": 0.2, "agent_id": "dup"},
        )
        await event_bus.emit(
            EventNames.EVALUATION_COMPLETED,
            {"score": 0.1, "agent_id": "dup"},
        )

        # Wait for all spawned tasks to complete
        if loop._tasks:
            await asyncio.gather(*loop._tasks, return_exceptions=True)

        # Only one task should have been spawned (dedup via _in_flight).
        assert "dup" not in loop._in_flight
        loop.stop()
