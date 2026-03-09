import pytest
from core.world_model.simulation import MCTSSimulator
from core.world_model.types import State, Action
from core.config.world_model import WorldModelConfig


@pytest.fixture
def simple_world():
    def get_actions(state):
        if state.variables.get("goal_reached"):
            return []
        return [
            Action(name="step", effects={"steps": state.variables.get("steps", 0) + 1})
        ]

    def apply_action(state, action):
        new_vars = state.variables.copy()
        new_vars.update(action.effects)
        if new_vars.get("steps", 0) >= 3:
            new_vars["goal_reached"] = True
        return State(name="next", variables=new_vars)

    def is_goal(state):
        return state.variables.get("goal_reached", False)

    return get_actions, apply_action, is_goal


@pytest.mark.asyncio
async def test_mcts_search_basic(simple_world):
    get_actions, apply_action, is_goal = simple_world

    config = WorldModelConfig(mcts_max_iterations=10, mcts_simulation_depth=5)

    simulator = MCTSSimulator(
        get_actions=get_actions,
        apply_action=apply_action,
        is_goal=is_goal,
        config=config,
    )

    initial_state = State(variables={"steps": 0})
    result = await simulator.search(initial_state)

    assert result.success
    assert result.best_path is not None
    assert result.iterations > 0


@pytest.mark.asyncio
async def test_what_if_analysis(simple_world):
    get_actions, apply_action, is_goal = simple_world

    simulator = MCTSSimulator(
        get_actions=get_actions, apply_action=apply_action, is_goal=is_goal
    )

    initial_state = State(variables={"steps": 0})
    action = Action(name="manual_step", effects={"steps": 1})

    result = await simulator.what_if(initial_state, action, depth=5)

    assert result.initial_state == initial_state
    # Check that first action in path is our forced action
    assert result.best_path.actions[0] == action
