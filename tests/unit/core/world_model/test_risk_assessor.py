from unittest.mock import MagicMock
from core.world_model.risk_assessor import RiskAssessor
from core.world_model.types import Action, ActionType, RiskLevel
from core.config.world_model import WorldModelConfig


def test_risk_assessor_default_config():
    assessor = RiskAssessor()
    action = Action(name="test", action_type=ActionType.DELETE)  # High risk type

    assessment = assessor.assess_action(action)
    assert assessment["level"] in [RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def test_risk_assessor_custom_config():
    config = WorldModelConfig(
        risk_weights={
            "action_type": 1.0,
            "reversibility": 0.0,
            "state_delta": 0.0,
            "uncertainty": 0.0,
        }
    )
    assessor = RiskAssessor(config=config)

    # Query should be minimal/low risk depending on normalization
    action = Action(name="test", action_type=ActionType.QUERY)
    assessment = assessor.assess_action(action)
    # With strict weights, it might be higher, check assessor logic
    # 1.0 * (1/5) = 0.2. + others 0. 0.2 score -> Low (0.2-0.4)
    assert assessment["level"] in [RiskLevel.MINIMAL, RiskLevel.LOW]


def test_assess_path():
    assessor = RiskAssessor()
    path_actions = [
        Action(name="a1", action_type=ActionType.QUERY),
        Action(name="a2", action_type=ActionType.EXECUTE),
    ]
    path = MagicMock(
        actions=path_actions
    )  # Mocking ActionPath properly would be better but simple list works for logic check if type matching isn't strict
    # Actually assess_path expects ActionPath object which has .actions attribute
    from core.world_model.types import ActionPath

    path = ActionPath(actions=path_actions)

    result = assessor.assess_path(path)
    assert result["cumulative"] is True
    assert result["score"] > 0
