import pytest
from unittest.mock import MagicMock, AsyncMock
from core.world_model.state_predictor import StatePredictor
from core.world_model.types import State, Action, ActionType
from core.services.llm import LLMService


@pytest.fixture
def mock_llm_service():
    service = MagicMock(spec=LLMService)
    service.generate_response = AsyncMock(return_value="VARIABLE: new_value")
    return service


@pytest.fixture
def predictor(mock_llm_service):
    return StatePredictor(llm_service=mock_llm_service)


@pytest.mark.asyncio
async def test_predict_simple_action(predictor):
    state = State(name="test", variables={"count": 1})
    action = Action(name="inc", action_type=ActionType.UPDATE, effects={"count": 2})

    new_state = await predictor.predict(state, action)
    assert new_state.variables["count"] == 2
    assert new_state.parent_id == state.id


@pytest.mark.asyncio
async def test_predict_with_llm(predictor, mock_llm_service):
    predictor.use_llm = True  # Although DI is used, we might want to ensure logic flow
    # Currently use_llm is implicitly True if llm_service is provided in updated code?
    # Let's check updated code logic: if self.llm_service -> use LLM.

    state = State(name="test", variables={"status": "old"})
    action = Action(name="complex_action", action_type=ActionType.EXECUTE)

    mock_llm_service.generate_response.return_value = "status: new_status"

    new_state = await predictor.predict(state, action)

    assert new_state.variables.get("status") == "new_status"
    mock_llm_service.generate_response.assert_called_once()


@pytest.mark.asyncio
async def test_predict_sequence(predictor):
    state = State(name="test", variables={"count": 0})
    actions = [
        Action(name="inc1", effects={"count": 1}),
        Action(name="inc2", effects={"count": 2}),
    ]

    transitions = await predictor.predict_sequence(state, actions)

    assert len(transitions) == 2
    assert transitions[0].target_state.variables["count"] == 1
    assert transitions[1].target_state.variables["count"] == 2


@pytest.mark.asyncio
async def test_compare_outcomes(predictor):
    state = State(name="test", variables={"count": 0})
    actions = [
        Action(name="opt1", effects={"count": 1}),
        Action(name="opt2", effects={"count": 5}),
    ]

    outcomes = await predictor.compare_outcomes(state, actions)

    assert outcomes["opt1"].variables["count"] == 1
    assert outcomes["opt2"].variables["count"] == 5
