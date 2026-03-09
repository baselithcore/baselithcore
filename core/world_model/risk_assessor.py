"""
Risk Assessor

Evaluates risks associated with actions and plans.
"""

from core.observability.logging import get_logger
from typing import Dict, List, Optional, Callable, Any

from .types import State, Action, ActionPath, RiskLevel

logger = get_logger(__name__)


class RiskAssessor:
    """
    Assesses risks of actions and action sequences.

    Evaluates:
    - Individual action risks
    - Cumulative path risks
    - State-specific risks
    - Reversibility

    Example:
        ```python
        assessor = RiskAssessor()
        risk = assessor.assess_action(action, current_state)
        path_risk = assessor.assess_path(action_path, initial_state)
        ```
    """

    def __init__(
        self,
        config: Optional[Any] = None,  # WorldModelConfig
        custom_assessor: Optional[Callable[[Action, State], float]] = None,
    ):
        """
        Initialize risk assessor.

        Args:
            config: WorldModelConfig instance
            custom_assessor: Custom risk assessment function
        """
        if config:
            self.weights = config.risk_weights
        else:
            from core.config.world_model import get_world_model_config

            self.weights = get_world_model_config().risk_weights

        self.custom_assessor = custom_assessor

        # Risk by action type
        self.action_type_risks = {
            "query": RiskLevel.MINIMAL,
            "execute": RiskLevel.MEDIUM,
            "create": RiskLevel.LOW,
            "update": RiskLevel.MEDIUM,
            "delete": RiskLevel.HIGH,
            "communicate": RiskLevel.LOW,
            "wait": RiskLevel.MINIMAL,
        }

    def assess_action(
        self,
        action: Action,
        state: Optional[State] = None,
    ) -> Dict:
        """
        Assess risk of a single action.

        Args:
            action: Action to assess
            state: Optional current state for context

        Returns:
            Risk assessment dict with score and details
        """
        if self.custom_assessor and state:
            score = self.custom_assessor(action, state)
            return {
                "score": score,
                "level": self._score_to_level(score),
                "details": {"custom_assessment": True},
            }

        # Calculate component risks
        type_risk = self._assess_action_type(action)
        reversibility_risk = self._assess_reversibility(action)
        state_delta_risk = self._assess_state_delta(action, state) if state else 0.3
        uncertainty_risk = self._assess_uncertainty(action)

        # Weighted combination
        score = (
            type_risk * self.weights["action_type"]
            + reversibility_risk * self.weights["reversibility"]
            + state_delta_risk * self.weights["state_delta"]
            + uncertainty_risk * self.weights["uncertainty"]
        )

        level = self._score_to_level(score)

        return {
            "score": score,
            "level": level,
            "details": {
                "type_risk": type_risk,
                "reversibility_risk": reversibility_risk,
                "state_delta_risk": state_delta_risk,
                "uncertainty_risk": uncertainty_risk,
            },
        }

    def assess_path(
        self,
        path: ActionPath,
        initial_state: Optional[State] = None,
    ) -> Dict:
        """
        Assess cumulative risk of an action path.

        Args:
            path: Action path to assess
            initial_state: Starting state

        Returns:
            Path risk assessment
        """
        if not path.actions:
            return {
                "score": 0.0,
                "level": RiskLevel.MINIMAL,
                "action_risks": [],
                "cumulative": False,
            }

        action_risks = []
        max_risk = 0.0
        cumulative_risk = 0.0

        for action in path.actions:
            assessment = self.assess_action(action, initial_state)
            action_risks.append(
                {
                    "action": action.name,
                    "score": assessment["score"],
                    "level": assessment["level"],
                }
            )
            max_risk = max(max_risk, assessment["score"])
            cumulative_risk += assessment["score"] * 0.5  # Diminishing returns

        # Path risk is combination of max and cumulative
        path_score = max_risk * 0.6 + min(cumulative_risk, 1.0) * 0.4

        return {
            "score": path_score,
            "level": self._score_to_level(path_score),
            "action_risks": action_risks,
            "max_single_risk": max_risk,
            "cumulative": True,
        }

    def _assess_action_type(self, action: Action) -> float:
        """Assess risk based on action type."""
        type_str = action.action_type.value
        level = self.action_type_risks.get(type_str, RiskLevel.MEDIUM)
        return level.value / 5.0  # Normalize to 0-1

    def _assess_reversibility(self, action: Action) -> float:
        """Assess risk based on reversibility."""
        if action.reversible:
            return action.risk_level.value / 5.0 * 0.5
        return action.risk_level.value / 5.0

    def _assess_state_delta(self, action: Action, state: State) -> float:
        """Assess risk based on expected state changes."""
        # More effects = more risk
        num_effects = len(action.effects)
        if num_effects == 0:
            return 0.1
        elif num_effects <= 2:
            return 0.3
        elif num_effects <= 5:
            return 0.5
        else:
            return 0.7

    def _assess_uncertainty(self, action: Action) -> float:
        """Assess risk due to uncertainty."""
        # Actions with fewer preconditions are more uncertain
        num_preconditions = len(action.preconditions)
        if num_preconditions >= 3:
            return 0.2  # Well-defined
        elif num_preconditions >= 1:
            return 0.4
        else:
            return 0.6  # Uncertain

    def _score_to_level(self, score: float) -> RiskLevel:
        """Convert numeric score to risk level."""
        if score < 0.2:
            return RiskLevel.MINIMAL
        elif score < 0.4:
            return RiskLevel.LOW
        elif score < 0.6:
            return RiskLevel.MEDIUM
        elif score < 0.8:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL

    def should_proceed(
        self,
        action: Action,
        state: Optional[State] = None,
        max_risk: RiskLevel = RiskLevel.MEDIUM,
    ) -> bool:
        """
        Determine if action is safe to proceed.

        Args:
            action: Action to check
            state: Current state
            max_risk: Maximum acceptable risk level

        Returns:
            True if safe to proceed
        """
        assessment = self.assess_action(action, state)
        return assessment["level"].value <= max_risk.value

    def filter_safe_actions(
        self,
        actions: List[Action],
        state: Optional[State] = None,
        max_risk: RiskLevel = RiskLevel.MEDIUM,
    ) -> List[Action]:
        """
        Filter actions to only safe ones.

        Args:
            actions: Actions to filter
            state: Current state
            max_risk: Maximum acceptable risk

        Returns:
            List of safe actions
        """
        return [
            action for action in actions if self.should_proceed(action, state, max_risk)
        ]
