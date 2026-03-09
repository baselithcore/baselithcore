"""
Unit Tests for World Model Module

Tests for predictive planning: state prediction, MCTS simulation, risk assessment, rollback.
"""

from core.world_model import (
    State,
    Action,
    RiskLevel,
    StatePredictor,
    MCTSSimulator,
    RiskAssessor,
    RollbackPlanner,
)
import pytest
from core.world_model.types import ActionType, ActionPath
from core.world_model.simulation import MCTSNode, MCTSConfig


# ============================================================================
# State Tests
# ============================================================================


class TestState:
    """Tests for State."""

    def test_creation(self):
        """Basic state creation."""
        state = State(name="test", variables={"x": 1, "y": 2})

        assert state.name == "test"
        assert state.get("x") == 1

    def test_set_creates_new_state(self):
        """Setting variable creates new state."""
        state1 = State(name="s1", variables={"x": 1})
        state2 = state1.set("x", 10)

        assert state1.get("x") == 1  # Original unchanged
        assert state2.get("x") == 10
        assert state2.parent_id == state1.id

    def test_copy(self):
        """Copy creates independent state."""
        state1 = State(name="s1", variables={"a": 1})
        state2 = state1.copy()

        state2.variables["a"] = 99

        assert state1.get("a") == 1
        assert state2.get("a") == 99

    def test_diff(self):
        """Diff identifies changes."""
        s1 = State(variables={"a": 1, "b": 2})
        s2 = State(variables={"a": 1, "b": 5, "c": 3})

        diff = s1.diff(s2)

        assert "b" in diff
        assert diff["b"] == (2, 5)
        assert "c" in diff
        assert "a" not in diff


# ============================================================================
# Action Tests
# ============================================================================


class TestAction:
    """Tests for Action."""

    def test_creation(self):
        """Basic action creation."""
        action = Action(
            name="test_action",
            action_type=ActionType.EXECUTE,
            effects={"done": True},
        )

        assert action.name == "test_action"
        assert action.reversible is True

    def test_can_apply(self):
        """Check precondition matching."""
        action = Action(
            name="conditional",
            preconditions={"ready": True},
        )

        state_valid = State(variables={"ready": True})
        state_invalid = State(variables={"ready": False})

        assert action.can_apply(state_valid)
        assert not action.can_apply(state_invalid)

    def test_apply(self):
        """Apply action effects."""
        action = Action(
            name="update",
            effects={"status": "completed", "count": 5},
        )
        state = State(variables={"status": "pending", "count": 0})

        new_state = action.apply(state)

        assert new_state.get("status") == "completed"
        assert new_state.get("count") == 5


# ============================================================================
# StatePredictor Tests
# ============================================================================


class TestStatePredictor:
    """Tests for StatePredictor."""

    @pytest.mark.asyncio
    async def test_predict_basic(self):
        """Basic prediction using action effects."""
        predictor = StatePredictor()
        state = State(variables={"x": 1})
        action = Action(effects={"x": 10, "y": 20})

        result = await predictor.predict(state, action)

        assert result.get("x") == 10
        assert result.get("y") == 20

    @pytest.mark.asyncio
    async def test_predict_precondition_fail(self):
        """Prediction fails on precondition mismatch."""
        predictor = StatePredictor()
        state = State(variables={"ready": False})
        action = Action(
            preconditions={"ready": True},
            effects={"done": True},
        )

        result = await predictor.predict(state, action)

        # Returns copy without applying effects
        assert result.get("done") is None

    @pytest.mark.asyncio
    async def test_predict_sequence(self):
        """Predict sequence of actions."""
        predictor = StatePredictor()
        state = State(variables={"count": 0})
        actions = [
            Action(name="inc1", effects={"count": 1}),
            Action(name="inc2", effects={"count": 2}),
            Action(name="inc3", effects={"count": 3}),
        ]

        transitions = await predictor.predict_sequence(state, actions)

        assert len(transitions) == 3
        assert transitions[-1].target_state.get("count") == 3

    @pytest.mark.asyncio
    async def test_compare_outcomes(self):
        """Compare outcomes of different actions."""
        predictor = StatePredictor()
        state = State(variables={"path": "unknown"})
        actions = [
            Action(name="left", effects={"path": "left"}),
            Action(name="right", effects={"path": "right"}),
        ]

        outcomes = await predictor.compare_outcomes(state, actions)

        assert outcomes["left"].get("path") == "left"
        assert outcomes["right"].get("path") == "right"


# ============================================================================
# MCTSSimulator Tests
# ============================================================================


class TestMCTSNode:
    """Tests for MCTSNode."""

    def test_ucb1_initial(self):
        """UCB1 for unvisited node is infinite."""
        node = MCTSNode(state=State())

        assert node.ucb1() == float("inf")

    def test_ucb1_after_visits(self):
        """UCB1 calculation after visits."""
        node = MCTSNode(state=State())
        node.visits = 10
        node.total_reward = 5.0

        ucb = node.ucb1()

        assert 0.4 < ucb < 0.6  # ~0.5 avg reward with some exploration

    def test_is_fully_expanded(self):
        """Check fully expanded state."""
        node = MCTSNode(
            state=State(),
            untried_actions=[Action(name="a1"), Action(name="a2")],
        )

        assert not node.is_fully_expanded

        node.untried_actions = []
        assert node.is_fully_expanded


class TestMCTSSimulator:
    """Tests for MCTSSimulator."""

    def get_test_simulator(self):
        """Create test simulator with simple domain."""

        def get_actions(state):
            if state.get("step", 0) >= 3:
                return []
            return [
                Action(name="step", effects={"step": state.get("step", 0) + 1}),
            ]

        def apply_action(state, action):
            return action.apply(state)

        def reward_fn(state):
            return state.get("step", 0) * 0.1

        def is_goal(state):
            return state.get("step", 0) >= 3

        return MCTSSimulator(
            get_actions=get_actions,
            apply_action=apply_action,
            reward_fn=reward_fn,
            is_goal=is_goal,
            config=MCTSConfig(max_iterations=50, time_limit=2.0),
        )

    @pytest.mark.asyncio
    async def test_search_finds_path(self):
        """Search finds path to goal."""
        simulator = self.get_test_simulator()
        initial = State(variables={"step": 0})

        result = await simulator.search(initial)

        assert result.success
        assert result.best_path is not None
        assert result.best_path.length > 0

    @pytest.mark.asyncio
    async def test_search_reaches_goal(self):
        """Search reaches goal state."""
        simulator = self.get_test_simulator()
        initial = State(variables={"step": 0})

        result = await simulator.search(initial)

        assert result.goal_reached

    @pytest.mark.asyncio
    async def test_what_if_analysis(self):
        """What-if analysis for specific action."""
        simulator = self.get_test_simulator()
        state = State(variables={"step": 0})
        action = Action(name="step", effects={"step": 1})

        result = await simulator.what_if(state, action, depth=2)

        assert result.success
        assert result.best_path.actions[0].name == "step"


# ============================================================================
# RiskAssessor Tests
# ============================================================================


class TestRiskAssessor:
    """Tests for RiskAssessor."""

    def test_assess_low_risk_action(self):
        """Low risk action assessment."""
        assessor = RiskAssessor()
        action = Action(
            name="query",
            action_type=ActionType.QUERY,
            risk_level=RiskLevel.MINIMAL,
            reversible=True,
        )

        result = assessor.assess_action(action)

        assert result["level"] in [RiskLevel.MINIMAL, RiskLevel.LOW]
        assert result["score"] < 0.4

    def test_assess_high_risk_action(self):
        """High risk action assessment."""
        assessor = RiskAssessor()
        action = Action(
            name="delete",
            action_type=ActionType.DELETE,
            risk_level=RiskLevel.HIGH,
            reversible=False,
        )

        result = assessor.assess_action(action)

        assert result["level"] in [RiskLevel.HIGH, RiskLevel.CRITICAL]
        assert result["score"] > 0.5

    def test_assess_path(self):
        """Assess action path risk."""
        assessor = RiskAssessor()
        path = ActionPath(
            actions=[
                Action(name="a1", risk_level=RiskLevel.LOW),
                Action(name="a2", risk_level=RiskLevel.MEDIUM),
            ]
        )

        result = assessor.assess_path(path)

        assert result["cumulative"]
        assert len(result["action_risks"]) == 2

    def test_should_proceed(self):
        """Should proceed check."""
        assessor = RiskAssessor()
        safe = Action(action_type=ActionType.QUERY, risk_level=RiskLevel.MINIMAL)
        dangerous = Action(action_type=ActionType.DELETE, risk_level=RiskLevel.CRITICAL)

        assert assessor.should_proceed(safe)
        assert not assessor.should_proceed(dangerous, max_risk=RiskLevel.LOW)

    def test_filter_safe_actions(self):
        """Filter to only safe actions."""
        assessor = RiskAssessor()
        actions = [
            Action(name="safe", action_type=ActionType.QUERY, risk_level=RiskLevel.LOW),
            Action(
                name="risky",
                action_type=ActionType.DELETE,
                risk_level=RiskLevel.HIGH,
                reversible=False,
            ),
        ]

        safe = assessor.filter_safe_actions(actions, max_risk=RiskLevel.MEDIUM)

        assert len(safe) == 1
        assert safe[0].name == "safe"


# ============================================================================
# RollbackPlanner Tests
# ============================================================================


class TestRollbackPlanner:
    """Tests for RollbackPlanner."""

    def test_create_rollback_reversible(self):
        """Create rollback for reversible action."""
        planner = RollbackPlanner()
        action = Action(
            name="update_value",
            action_type=ActionType.UPDATE,
            effects={"value": 100},
            reversible=True,
        )
        state = State(variables={"value": 50})

        plan = planner.create_rollback(action, state)

        assert plan.can_rollback
        assert len(plan.rollback_actions) >= 1

    def test_create_rollback_irreversible(self):
        """Irreversible actions have no rollback."""
        planner = RollbackPlanner()
        action = Action(
            name="irreversible",
            reversible=False,
        )
        state = State()

        plan = planner.create_rollback(action, state)

        assert not plan.can_rollback
        assert plan.feasibility == 0.0

    def test_sequence_rollback(self):
        """Create rollback for action sequence."""
        planner = RollbackPlanner()
        actions = [
            Action(
                name="step1",
                action_type=ActionType.UPDATE,
                effects={"a": 1},
                reversible=True,
            ),
            Action(
                name="step2",
                action_type=ActionType.UPDATE,
                effects={"b": 2},
                reversible=True,
            ),
        ]
        state = State(variables={"a": 0, "b": 0})

        plans = planner.create_sequence_rollback(actions, state)

        # Should be in reverse order
        assert len(plans) == 2
        assert plans[0].original_action.name == "step2"
        assert plans[1].original_action.name == "step1"

    def test_record_action(self):
        """Record action for history."""
        planner = RollbackPlanner()
        action = Action(name="test")
        state = State()

        planner.record_action(action, state)

        assert planner.get_history_length() == 1


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
async def test_predictive_planning_workflow():
    """Full predictive planning workflow."""

    # Setup domain
    def get_actions(state):
        actions = []
        if state.get("progress", 0) < 100:
            actions.append(
                Action(
                    name="advance",
                    effects={"progress": min(state.get("progress", 0) + 25, 100)},
                    risk_level=RiskLevel.LOW,
                )
            )
        if state.get("progress", 0) >= 50:
            actions.append(
                Action(
                    name="finish",
                    action_type=ActionType.EXECUTE,
                    effects={"done": True, "progress": 100},
                    risk_level=RiskLevel.MEDIUM,
                )
            )
        return actions

    def apply_action(state, action):
        return action.apply(state)

    def is_goal(state):
        return state.get("done") is True

    # Create components
    simulator = MCTSSimulator(
        get_actions=get_actions,
        apply_action=apply_action,
        is_goal=is_goal,
        config=MCTSConfig(max_iterations=100),
    )
    risk_assessor = RiskAssessor()

    # Initial state
    initial = State(name="project", variables={"progress": 0, "done": False})

    # Search for path
    result = await simulator.search(initial)

    assert result.success
    assert result.best_path is not None

    # Assess path risk
    path_risk = risk_assessor.assess_path(result.best_path)
    assert path_risk["level"].value <= RiskLevel.HIGH.value

    # Verify goal reached
    final = result.final_state
    assert final.get("done") is True or final.get("progress") >= 100
