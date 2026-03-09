import pytest
from unittest.mock import AsyncMock

from core.memory.manager import AgentMemory
from core.memory.types import MemoryType
from core.human.interaction import HumanIntervention
from core.learning.feedback import FeedbackCollector
from core.optimization.optimizer import PromptOptimizer

# --- MEMORY TESTS ---


@pytest.fixture
def memory_manager():
    return AgentMemory()


@pytest.mark.asyncio
async def test_memory_short_term_buffer(memory_manager):
    """Test that short term memory is buffered."""
    await memory_manager.add_memory("Hello world", memory_type=MemoryType.SHORT_TERM)

    memories = await memory_manager.recall("Hello")
    assert len(memories) >= 1
    assert memories[0].content == "Hello world"
    assert memories[0].memory_type == MemoryType.SHORT_TERM


@pytest.mark.asyncio
async def test_memory_consolidation_mock(memory_manager):
    """Test consolidation calls provider."""
    mock_provider = AsyncMock()
    memory_manager.provider = mock_provider

    await memory_manager.add_memory("Important fact", memory_type=MemoryType.SHORT_TERM)
    await memory_manager.consolidate()

    # Should have added to provider
    assert mock_provider.add.called


# --- HUMAN TESTS ---


@pytest.mark.asyncio
async def test_human_approval():
    intervention = HumanIntervention()

    # Mock callback to auto-approve
    async def auto_approve(request):
        return True

    intervention.callback = auto_approve

    result = await intervention.request_approval("Deploy to prod?")
    assert result is True


@pytest.mark.asyncio
async def test_human_input():
    intervention = HumanIntervention()

    # Mock callback to return input
    async def provide_input(request):
        return "SecretCode"

    intervention.callback = provide_input

    result = await intervention.ask_input("Enter code")
    assert result == "SecretCode"


# --- LEARNING TESTS ---


@pytest.mark.asyncio
async def test_feedback_collection():
    collector = FeedbackCollector()
    agent_id = "agent_1"

    await collector.log_feedback(agent_id, "task_1", 0.9, "Good job")
    await collector.log_feedback(agent_id, "task_2", 0.1, "Bad job")

    stats = await collector.get_agent_performance(agent_id)
    assert stats["count"] == 2
    assert stats["average_score"] == 0.5


# --- OPTIMIZATION TESTS ---


@pytest.mark.asyncio
async def test_optimization_analysis():
    collector = FeedbackCollector()
    optimizer = PromptOptimizer(collector)
    agent_id = "agent_bad"

    # Add negative feedback
    await collector.log_feedback(agent_id, "task_1", 0.2, "Too slow")

    suggestions = await optimizer.analyze_performance(threshold=0.5)

    assert len(suggestions) == 1
    assert suggestions[0].agent_id == agent_id
    assert "Too slow" in suggestions[0].suggestion
