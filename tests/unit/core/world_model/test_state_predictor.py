from unittest.mock import AsyncMock, MagicMock

import pytest

from core.services.llm import LLMService
from core.world_model.state_predictor import StatePredictor
from core.world_model.types import Action, ActionType, State


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


async def test_compare_outcomes_predicts_alternatives_concurrently():
    """Every alternative starts from the SAME state — the predictions are
    independent LLM round-trips and must overlap."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from core.world_model.state_predictor import StatePredictor

    predictor = StatePredictor(llm_service=MagicMock())
    in_flight = 0
    max_in_flight = 0

    async def slow_predict(state, action, context=None):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return MagicMock(name=f"state-after-{action.name}")

    predictor.predict = AsyncMock(side_effect=slow_predict)
    actions = [MagicMock(name=f"a{i}") for i in range(4)]
    for i, a in enumerate(actions):
        a.name = f"action-{i}"

    outcomes = await predictor.compare_outcomes(MagicMock(), actions)

    assert max_in_flight > 1
    assert set(outcomes.keys()) == {f"action-{i}" for i in range(4)}
