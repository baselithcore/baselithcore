"""
Reward Attribution and Signaling Module.

Defines the success criteria for agent actions. Combines explicit
rule-based rewards with learned value models to provide a holistic
signal for the policy optimization process.
"""

from typing import Dict, List, Callable

from core.observability.logging import get_logger
from .types import Experience, Reward

logger = get_logger(__name__)


class RewardModel:
    """
    Predictive engine for action utility.

    Calculates the 'goodness' of an experience by synthesizing success
    conditions, efficiency metrics, and direct human feedback. This
    multi-objective signal is used by the learner to steer agent behavior.
    """

    def __init__(
        self,
        default_reward: float = 0.0,
        learning_rate: float = 0.01,
    ):
        """
        Initialize reward model.

        Args:
            default_reward: Default reward value
            learning_rate: Learning rate for updates
        """
        self.default_reward = default_reward
        self.learning_rate = learning_rate

        # Reward rules: name -> (condition_fn, reward_fn)
        self._rules: Dict[str, tuple] = {}

        # Learned action values
        self._action_values: Dict[str, float] = {}

        # Reward history for learning
        self._history: List[tuple] = []

        # Setup default rules
        self._setup_default_rules()

    def _setup_default_rules(self) -> None:
        """Setup default reward rules."""
        # Success bonus
        self.add_rule(
            "success",
            condition=lambda e: e.success,
            reward=lambda e: 1.0,
        )

        # Failure penalty
        self.add_rule(
            "failure",
            condition=lambda e: not e.success and e.outcome == "error",
            reward=lambda e: -0.5,
        )

        # Efficiency bonus
        self.add_rule(
            "efficiency",
            condition=lambda e: e.metadata.get("steps", 10) < 5,
            reward=lambda e: 0.2,
        )

    def add_rule(
        self,
        name: str,
        condition: Callable[[Experience], bool],
        reward: Callable[[Experience], float],
        weight: float = 1.0,
    ) -> None:
        """
        Add a reward rule.

        Args:
            name: Rule identifier
            condition: Function to check if rule applies
            reward: Function to calculate reward
            weight: Rule weight
        """
        self._rules[name] = (condition, reward, weight)

    def remove_rule(self, name: str) -> bool:
        """Remove a reward rule."""
        if name in self._rules:
            del self._rules[name]
            return True
        return False

    def calculate_reward(
        self,
        experience: Experience,
        include_learned: bool = True,
    ) -> Reward:
        """
        Calculate reward for an experience.

        Args:
            experience: The experience
            include_learned: Include learned action values

        Returns:
            Calculated reward
        """
        total_reward = self.default_reward
        reasons = []

        # Apply rules
        for name, (condition, reward_fn, weight) in self._rules.items():
            try:
                if condition(experience):
                    rule_reward = reward_fn(experience) * weight
                    total_reward += rule_reward
                    reasons.append(f"{name}: {rule_reward:+.2f}")
            except Exception as e:
                logger.warning(f"Rule {name} failed: {e}")

        # Add learned value
        if include_learned and experience.action in self._action_values:
            learned = self._action_values[experience.action]
            total_reward += learned * 0.3  # Blend with learned
            reasons.append(f"learned: {learned:+.2f}")

        return Reward(
            value=total_reward,
            reason="; ".join(reasons) if reasons else "default",
            source="model",
        )

    def update_from_feedback(
        self,
        experience: Experience,
        human_reward: float,
    ) -> None:
        """
        Update model from human feedback.

        Args:
            experience: The experience
            human_reward: Human-provided reward
        """
        # Update action value
        current = self._action_values.get(experience.action, 0.0)
        updated = current + self.learning_rate * (human_reward - current)
        self._action_values[experience.action] = updated

        # Store history
        self._history.append((experience.action, human_reward))

        logger.info(
            f"Updated action {experience.action}: {current:.2f} -> {updated:.2f}"
        )

    def batch_update(self, experiences: List[Experience]) -> None:
        """
        Batch update from experiences.

        Args:
            experiences: List of experiences
        """
        # Group by action
        action_rewards: Dict[str, List[float]] = {}

        for exp in experiences:
            reward = self.calculate_reward(exp, include_learned=False)
            if exp.action not in action_rewards:
                action_rewards[exp.action] = []
            action_rewards[exp.action].append(reward.value)

        # Update action values
        for action, rewards in action_rewards.items():
            avg_reward = sum(rewards) / len(rewards)
            current = self._action_values.get(action, 0.0)
            updated = current + self.learning_rate * (avg_reward - current)
            self._action_values[action] = updated

    def get_action_value(self, action: str) -> float:
        """Get learned value for an action."""
        return self._action_values.get(action, self.default_reward)

    def get_best_action(self, actions: List[str]) -> str:
        """Get action with highest learned value."""
        if not actions:
            return ""
        return max(actions, key=lambda a: self.get_action_value(a))

    def get_stats(self) -> Dict:
        """Get model statistics."""
        return {
            "num_rules": len(self._rules),
            "num_actions": len(self._action_values),
            "history_size": len(self._history),
            "avg_action_value": (
                sum(self._action_values.values()) / len(self._action_values)
                if self._action_values
                else 0
            ),
        }
