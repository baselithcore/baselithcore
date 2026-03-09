import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from core.orchestration.orchestrator import Orchestrator
from core.orchestration.handlers.reasoning import ReasoningHandler
from core.evaluation.service import EvaluationService
from core.events import EventNames, get_event_bus
from core.evaluation.protocols import EvaluationResult, QualityLevel


@pytest.fixture
def mock_llm_service():
    mock = MagicMock()

    # Async mock for ToT
    async def mock_generate(prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "judge" in prompt_lower or "evaluate" in prompt_lower:
            return "0.95"
        else:
            return "1. Thought one\n2. Thought two"

    mock.generate_response_async = AsyncMock(side_effect=mock_generate)
    # Sync fallback
    mock.generate_response.return_value = "0.5"
    return mock


@pytest.fixture
def mock_evaluator():
    evaluator = MagicMock()
    result = EvaluationResult(
        score=0.95,
        quality=QualityLevel.EXCELLENT,
        feedback="Perfect reasoning",
        aspects={"logic": 1.0},
    )
    evaluator.evaluate = AsyncMock(return_value=result)
    return evaluator


@pytest.mark.asyncio
async def test_end_to_end_reasoning_flow(mock_llm_service, mock_evaluator):
    """
    Verify full flow:
    1. User Query -> Orchestrator
    2. Intent -> ReasoningHandler (ToT)
    3. ReasoningHandler -> LLM (Mocked)
    4. Result -> EventBus (FLOW_COMPLETED)
    5. EvaluationService -> Evaluator -> EventBus (EVALUATION_COMPLETED)
    """

    # 1. Setup Event Bus
    event_bus = get_event_bus()  # Singleton
    # Clear listeners to be clean
    # event_bus._listeners = {} # Dangerous if implementation changes, but ok for test isolation if needed.
    # Better to just use it.

    # 2. Setup Evaluation Service
    eval_service = EvaluationService(event_bus=event_bus, evaluator=mock_evaluator)

    # Force enable
    with patch("core.config.evaluation.evaluation_config") as mock_config:
        mock_config.enabled = True
        eval_service.start()

        # 3. Setup Orchestrator
        # Mock Intent Classifier to force "complex_reasoning"
        mock_classifier = MagicMock()
        mock_classifier.classify = AsyncMock(return_value="complex_reasoning")

        orchestrator = Orchestrator(
            intent_classifier=mock_classifier, default_intent="qa_docs"
        )

        # Register Reasoning Handler manually (since we don't have full plugin registry in test)
        # We need to ensure get_llm_service returns our mock inside the handler
        with patch(
            "core.orchestration.handlers.reasoning.get_llm_service",
            return_value=mock_llm_service,
        ):
            handler = ReasoningHandler()
            orchestrator.register_handler("complex_reasoning", handler)

            # Subscribe to EVALUATION_COMPLETED to verify flow end
            future_eval = asyncio.Future()

            async def on_eval(data):
                if not future_eval.done():
                    future_eval.set_result(data)

            event_bus.subscribe(EventNames.EVALUATION_COMPLETED, on_eval)

            # 4. EXECUTE FLOW
            query = "Solve this complex logic puzzle"
            result = await orchestrator.process(query)

            # 5. Verify Orchestrator Result
            assert result["intent"] == "complex_reasoning"
            assert "steps" in result  # From ReasoningHandler
            assert not result.get("error")

            # 6. Verify Evaluation Triggered
            try:
                eval_data = await asyncio.wait_for(future_eval, timeout=3.0)
            except asyncio.TimeoutError:
                pytest.fail("Evaluation event was not emitted in time")

            assert eval_data["intent"] == "complex_reasoning"
            assert eval_data["score"] == 0.95

            eval_service.stop()
