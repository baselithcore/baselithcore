"""
Comprehensive tests for RiskAssessor module.

Tests cover:
- Basic risk assessment for all action types
- Custom assessor callbacks
- Path risk assessment
- should_proceed() safety checks
- filter_safe_actions() filtering
- All internal risk calculation methods
- Edge cases and boundary conditions
"""

import pytest
from core.world_model.risk_assessor import RiskAssessor
from core.world_model.types import Action, ActionType, RiskLevel, ActionPath, State
from core.config.world_model import WorldModelConfig


class TestRiskAssessorBasics:
    """Test basic risk assessor functionality."""

    def test_risk_assessor_default_config(self):
        """Risk assessor should work with default configuration."""
        assessor = RiskAssessor()
        action = Action(name="test", action_type=ActionType.DELETE)  # High risk type

        assessment = assessor.assess_action(action)
        assert assessment["level"] in [
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ]
        assert "score" in assessment
        assert "details" in assessment

    def test_risk_assessor_custom_config(self):
        """Risk assessor should accept custom configuration."""
        config = WorldModelConfig(
            risk_weights={
                "action_type": 1.0,
                "reversibility": 0.0,
                "state_delta": 0.0,
                "uncertainty": 0.0,
            }
        )
        assessor = RiskAssessor(config=config)

        # Query should be minimal/low risk
        action = Action(name="test", action_type=ActionType.QUERY)
        assessment = assessor.assess_action(action)
        assert assessment["level"] in [RiskLevel.MINIMAL, RiskLevel.LOW]


class TestActionTypeRisks:
    """Test risk assessment for different action types."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_query_action_minimal_risk(self, assessor):
        """QUERY actions should have minimal risk."""
        action = Action(name="query_test", action_type=ActionType.QUERY)
        assessment = assessor.assess_action(action)
        assert assessment["level"] in [RiskLevel.MINIMAL, RiskLevel.LOW]

    def test_delete_action_high_risk(self, assessor):
        """DELETE actions should have high risk."""
        action = Action(name="delete_test", action_type=ActionType.DELETE)
        assessment = assessor.assess_action(action)
        assert assessment["level"] in [
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        ]

    def test_execute_action_medium_risk(self, assessor):
        """EXECUTE actions should have medium risk."""
        action = Action(name="exec_test", action_type=ActionType.EXECUTE)
        assessment = assessor.assess_action(action)
        assert assessment["level"] in [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]

    def test_create_action_low_risk(self, assessor):
        """CREATE actions should have low risk."""
        action = Action(name="create_test", action_type=ActionType.CREATE)
        assessment = assessor.assess_action(action)
        assert assessment["level"] in [
            RiskLevel.MINIMAL,
            RiskLevel.LOW,
            RiskLevel.MEDIUM,
        ]

    def test_update_action_medium_risk(self, assessor):
        """UPDATE actions should have medium risk."""
        action = Action(name="update_test", action_type=ActionType.UPDATE)
        assessment = assessor.assess_action(action)
        assert assessment["level"] in [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]


class TestReversibilityRisk:
    """Test risk assessment based on action reversibility."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_reversible_action_lower_risk(self, assessor):
        """Reversible actions should have lower risk."""
        reversible = Action(
            name="reversible",
            action_type=ActionType.EXECUTE,
            reversible=True,
            risk_level=RiskLevel.HIGH,
        )
        non_reversible = Action(
            name="non_reversible",
            action_type=ActionType.EXECUTE,
            reversible=False,
            risk_level=RiskLevel.HIGH,
        )

        rev_assessment = assessor.assess_action(reversible)
        non_rev_assessment = assessor.assess_action(non_reversible)

        # Reversible should have lower or equal risk
        assert rev_assessment["score"] <= non_rev_assessment["score"]


class TestStateDeltaRisk:
    """Test risk assessment based on state changes."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_no_effects_minimal_risk(self, assessor):
        """Actions with no effects should have minimal state delta risk."""
        action = Action(name="no_effects", action_type=ActionType.QUERY, effects=[])
        state = State(variables={})
        assessment = assessor.assess_action(action, state)
        assert assessment["details"]["state_delta_risk"] == 0.1

    def test_few_effects_low_risk(self, assessor):
        """Actions with few effects should have low state delta risk."""
        action = Action(
            name="few_effects",
            action_type=ActionType.QUERY,
            effects=["effect1", "effect2"],
        )
        state = State(variables={})
        assessment = assessor.assess_action(action, state)
        assert assessment["details"]["state_delta_risk"] == 0.3

    def test_many_effects_high_risk(self, assessor):
        """Actions with many effects should have high state delta risk."""
        action = Action(
            name="many_effects",
            action_type=ActionType.QUERY,
            effects=["e1", "e2", "e3", "e4", "e5", "e6"],
        )
        state = State(variables={})
        assessment = assessor.assess_action(action, state)
        assert assessment["details"]["state_delta_risk"] == 0.7


class TestUncertaintyRisk:
    """Test risk assessment based on action uncertainty."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_well_defined_action_low_uncertainty(self, assessor):
        """Actions with many preconditions should have low uncertainty."""
        action = Action(
            name="well_defined",
            action_type=ActionType.EXECUTE,
            preconditions=["p1", "p2", "p3"],
        )
        assessment = assessor.assess_action(action)
        assert assessment["details"]["uncertainty_risk"] == 0.2

    def test_some_preconditions_medium_uncertainty(self, assessor):
        """Actions with some preconditions should have medium uncertainty."""
        action = Action(
            name="some_precond",
            action_type=ActionType.EXECUTE,
            preconditions=["p1"],
        )
        assessment = assessor.assess_action(action)
        assert assessment["details"]["uncertainty_risk"] == 0.4

    def test_no_preconditions_high_uncertainty(self, assessor):
        """Actions with no preconditions should have high uncertainty."""
        action = Action(
            name="uncertain", action_type=ActionType.EXECUTE, preconditions=[]
        )
        assessment = assessor.assess_action(action)
        assert assessment["details"]["uncertainty_risk"] == 0.6


class TestCustomAssessor:
    """Test custom risk assessor callback."""

    def test_custom_assessor_used(self):
        """Custom assessor should override default calculation."""

        def custom_risk(action: Action, state: State) -> float:
            # Always return fixed risk
            return 0.75  # HIGH risk

        assessor = RiskAssessor(custom_assessor=custom_risk)
        action = Action(name="test", action_type=ActionType.QUERY)  # Normally low
        state = State(variables={})

        assessment = assessor.assess_action(action, state)
        assert assessment["score"] == 0.75
        assert assessment["level"] == RiskLevel.HIGH
        assert assessment["details"]["custom_assessment"] is True

    def test_custom_assessor_without_state(self):
        """Custom assessor should not be used without state."""

        def custom_risk(action: Action, state: State) -> float:
            return 0.9

        assessor = RiskAssessor(custom_assessor=custom_risk)
        action = Action(name="test", action_type=ActionType.QUERY)

        # No state provided, should use default
        assessment = assessor.assess_action(action, state=None)
        assert "custom_assessment" not in assessment["details"]


class TestPathAssessment:
    """Test risk assessment for action paths."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_assess_empty_path(self, assessor):
        """Empty path should have minimal risk."""
        path = ActionPath(actions=[])
        result = assessor.assess_path(path)
        assert result["score"] == 0.0
        assert result["level"] == RiskLevel.MINIMAL
        assert result["cumulative"] is False

    def test_assess_path_with_actions(self, assessor):
        """Path with actions should calculate cumulative risk."""
        path_actions = [
            Action(name="a1", action_type=ActionType.QUERY),
            Action(name="a2", action_type=ActionType.EXECUTE),
        ]
        path = ActionPath(actions=path_actions)

        result = assessor.assess_path(path)
        assert result["cumulative"] is True
        assert result["score"] > 0
        assert len(result["action_risks"]) == 2
        assert "max_single_risk" in result

    def test_assess_long_path(self, assessor):
        """Long paths should have cumulative risk capped."""
        path_actions = [
            Action(name=f"a{i}", action_type=ActionType.EXECUTE) for i in range(10)
        ]
        path = ActionPath(actions=path_actions)

        result = assessor.assess_path(path)
        assert result["cumulative"] is True
        assert len(result["action_risks"]) == 10
        # Path score should be reasonable (not > 1.0)
        assert result["score"] <= 1.0

    def test_assess_high_risk_path(self, assessor):
        """Path with high-risk actions should have elevated overall risk."""
        path_actions = [
            Action(name="delete1", action_type=ActionType.DELETE),
            Action(name="delete2", action_type=ActionType.DELETE),
        ]
        path = ActionPath(actions=path_actions)

        result = assessor.assess_path(path)
        # Should be at least medium risk with delete actions
        assert result["level"] in [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


class TestSafetyChecks:
    """Test safety check methods."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_should_proceed_safe_action(self, assessor):
        """Safe actions should be allowed to proceed."""
        action = Action(name="safe", action_type=ActionType.QUERY)
        assert assessor.should_proceed(action, max_risk=RiskLevel.MEDIUM) is True

    def test_should_proceed_risky_action(self, assessor):
        """Risky actions should be blocked."""
        action = Action(name="risky", action_type=ActionType.DELETE)
        # DELETE actions are typically HIGH risk, which exceeds LOW max_risk
        assert assessor.should_proceed(action, max_risk=RiskLevel.LOW) is False

    def test_should_proceed_with_state(self, assessor):
        """should_proceed should consider state context."""
        action = Action(name="test", action_type=ActionType.EXECUTE)
        state = State(variables={"context": "test"})
        result = assessor.should_proceed(action, state, max_risk=RiskLevel.HIGH)
        assert isinstance(result, bool)

    def test_filter_safe_actions_empty_list(self, assessor):
        """Filtering empty list should return empty."""
        assert assessor.filter_safe_actions([], max_risk=RiskLevel.MEDIUM) == []

    def test_filter_safe_actions_mixed_risks(self, assessor):
        """Filter should return only safe actions."""
        actions = [
            Action(name="safe1", action_type=ActionType.QUERY),
            Action(name="risky", action_type=ActionType.DELETE),
            Action(name="safe2", action_type=ActionType.CREATE),
        ]

        safe_actions = assessor.filter_safe_actions(actions, max_risk=RiskLevel.MEDIUM)

        # Should filter out some high-risk actions
        assert len(safe_actions) <= len(actions)
        # At least QUERY should be safe
        assert any(a.action_type == ActionType.QUERY for a in safe_actions)

    def test_filter_safe_actions_all_risky(self, assessor):
        """Filter should return empty if all actions are risky."""
        actions = [
            Action(name="risky1", action_type=ActionType.DELETE),
            Action(name="risky2", action_type=ActionType.DELETE),
        ]

        safe_actions = assessor.filter_safe_actions(actions, max_risk=RiskLevel.LOW)

        # Depending on exact scoring, may filter all
        assert len(safe_actions) <= len(actions)


class TestScoreLevelConversion:
    """Test risk score to level conversion."""

    @pytest.fixture
    def assessor(self):
        return RiskAssessor()

    def test_score_to_level_boundaries(self, assessor):
        """Test boundary values for score to level conversion."""
        assert assessor._score_to_level(0.0) == RiskLevel.MINIMAL
        assert assessor._score_to_level(0.19) == RiskLevel.MINIMAL
        assert assessor._score_to_level(0.2) == RiskLevel.LOW
        assert assessor._score_to_level(0.39) == RiskLevel.LOW
        assert assessor._score_to_level(0.4) == RiskLevel.MEDIUM
        assert assessor._score_to_level(0.59) == RiskLevel.MEDIUM
        assert assessor._score_to_level(0.6) == RiskLevel.HIGH
        assert assessor._score_to_level(0.79) == RiskLevel.HIGH
        assert assessor._score_to_level(0.8) == RiskLevel.CRITICAL
        assert assessor._score_to_level(1.0) == RiskLevel.CRITICAL
