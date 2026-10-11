import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from baselith.core.config.evaluation import EvaluationConfig
from baselith.core.evaluation.protocols import EvaluationResult, QualityLevel
from baselith.core.evaluation.service import EvaluationService
from baselith.core.events import EventBus, EventNames


@pytest.fixture
def mock_event_bus():
    return EventBus()


@pytest.fixture
def mock_evaluator():
    evaluator = MagicMock()
    # Setup async evaluate return
    result = EvaluationResult(
        score=0.9,
        quality=QualityLevel.EXCELLENT,
        feedback="Great job",
        should_refine=False,
        aspects={"relevance": 1.0},
    )
    evaluator.evaluate = AsyncMock(return_value=result)
    return evaluator


@pytest.mark.asyncio
async def test_evaluation_service_flow(mock_event_bus, mock_evaluator):
    """Test that EvaluationService listens to FLOW_COMPLETED and triggers evaluation."""

    # 1. Initialize Service: every flow sampled and judged.
    config = EvaluationConfig(
        enabled=True, online_sample_rate=1.0, online_judge_rate=1.0
    )
    service = EvaluationService(
        event_bus=mock_event_bus, evaluator=mock_evaluator, config=config
    )

    service.start()

    # 2. Simulate FLOW_COMPLETED event
    event_data = {
        "intent": "test_intent",
        "success": True,
        "query": "Hello",
        "response": "World",
        "context": {},
        "run_id": "run-1",
    }

    # We need to capture the EVALUATION_COMPLETED event
    # Let's subscribe a test listener
    future = asyncio.Future()

    async def on_eval_complete(data):
        future.set_result(data)

    mock_event_bus.subscribe(EventNames.EVALUATION_COMPLETED, on_eval_complete)

    # Emit the trigger event
    await mock_event_bus.emit(EventNames.FLOW_COMPLETED, event_data)

    # 3. Wait for evaluation to complete (with timeout)
    try:
        result_data = await asyncio.wait_for(future, timeout=2.0)
    except TimeoutError:
        pytest.fail("Evaluation did not complete in time")

    # 4. Verify results
    assert result_data["intent"] == "test_intent"
    assert result_data["score"] == 0.9
    assert result_data["quality"] == "excellent"

    # Verify evaluator was called with correct data
    mock_evaluator.evaluate.assert_called_once()
    args = mock_evaluator.evaluate.call_args
    assert args.kwargs["query"] == "Hello"
    assert args.kwargs["response"] == "World"

    await service.shutdown()
