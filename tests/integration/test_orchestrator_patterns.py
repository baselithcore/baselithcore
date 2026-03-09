import pytest
from unittest.mock import MagicMock, AsyncMock

from core.orchestration.orchestrator import Orchestrator
from core.memory.manager import AgentMemory
from core.human.interaction import HumanIntervention
from core.learning.feedback import FeedbackCollector


@pytest.fixture
def orchestrator_deps():
    memory = AgentMemory()
    human = HumanIntervention()
    learning = FeedbackCollector()
    mock_llm = MagicMock()
    mock_llm.generate = AsyncMock(
        return_value={"content": "Test response", "type": "final"}
    )

    return memory, human, learning, mock_llm


@pytest.mark.asyncio
async def test_orchestrator_initialization_with_patterns(orchestrator_deps):
    memory, human, learning, mock_llm = orchestrator_deps

    orchestrator = Orchestrator(
        memory_manager=memory, human_intervention=human, feedback_collector=learning
    )

    assert orchestrator.memory_manager == memory
    assert orchestrator.human_intervention == human
    assert orchestrator.feedback_collector == learning


@pytest.mark.asyncio
async def test_orchestrator_process_flow_integration(orchestrator_deps):
    """
    Test that the orchestrator process flow utilizes the components.
    This is a high-level integration test.
    """
    memory, human, learning, mock_llm = orchestrator_deps

    # Mocking internal components for isolation
    # IntentClassifier.classify is ASYNC and returns a STRING
    mock_classifier = MagicMock()
    mock_classifier.classify = AsyncMock(return_value="chat")

    orchestrator = Orchestrator(
        intent_classifier=mock_classifier,
        memory_manager=memory,
        human_intervention=human,
        feedback_collector=learning,
    )

    # Handler.handle is ASYNC
    mock_handler = AsyncMock()
    mock_handler.handle.return_value = {"content": "Handled", "type": "final"}
    orchestrator._flow_handlers = {"chat": mock_handler}

    # Run process
    user_input = "Hello agent"
    context = {"user_id": "user1", "session_id": "sess1"}
    await orchestrator.process(user_input, context=context)

    # Verify handler was called (handle, not process)
    mock_handler.handle.assert_called_once()
