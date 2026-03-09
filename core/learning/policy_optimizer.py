"""
Behavioral Policy Optimization Module.

Specializes in refining agent logic based on rewards. Implements
algorithms for balancing exploration with exploitation, ensuring the
agent discovers optimal strategies while maintaining reliable performance.
"""

import random
from typing import Dict, List, Optional, Any

from core.observability.logging import get_logger
from .types import Experience, LearningMetrics
from .experience_buffer import ExperienceReplay
from .reward_model import RewardModel

logger = get_logger(__name__)


class PolicyOptimizer:
    """
    Manager for agent decision-making logic.

    Utilizes Q-learning techniques and policy gradient approximations to
    update the agent's action preferences. Supports behavioral cloning to
    initialize agents with expert demonstrations.
    """

    def __init__(
        self,
        experience_buffer: Optional[ExperienceReplay] = None,
        reward_model: Optional[RewardModel] = None,
        epsilon: float = 0.1,
        learning_rate: float = 0.01,
        discount_factor: float = 0.95,
    ):
        """
        Initialize policy optimizer.

        Args:
            experience_buffer: Experience replay buffer
            reward_model: Reward prediction model
            epsilon: Exploration rate
            learning_rate: Learning rate
            discount_factor: Discount for future rewards
        """
        self.buffer = experience_buffer or ExperienceReplay()
        self.reward_model = reward_model or RewardModel()
        self.epsilon = epsilon
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor

        # State-action values (Q-table style)
        self._q_values: Dict[str, Dict[str, float]] = {}

        # Action preferences (for policy gradient)
        self._preferences: Dict[str, float] = {}

        # Metrics
        self.metrics = LearningMetrics()

    def select_action(
        self,
        state: Dict[str, Any],
        available_actions: List[str],
        explore: bool = True,
    ) -> str:
        """
        Select action using epsilon-greedy policy.

        Args:
            state: Current state
            available_actions: Available actions
            explore: Allow exploration

        Returns:
            Selected action
        """
        if not available_actions:
            return ""

        # Exploration
        if explore and random.random() < self.epsilon:  # nosec B311
            return random.choice(available_actions)  # nosec B311

        # Exploitation - use Q-values
        state_key = self._state_to_key(state)
        q_values = self._get_q_values(state_key, available_actions)

        # Select best action
        best_action = max(available_actions, key=lambda a: q_values.get(a, 0.0))

        return best_action

    def update(self, experience: Experience) -> None:
        """
        Update policy based on experience.

        Args:
            experience: New experience
        """
        # Add to buffer
        self.buffer.add(experience)

        # Calculate reward if not set
        if experience.reward == 0:
            reward = self.reward_model.calculate_reward(experience)
            experience.reward = reward.value

        # Update Q-values
        self._update_q_value(experience)

        # Update preferences
        self._update_preferences(experience)

        # Update metrics
        self.metrics.update(experience)

    def _update_q_value(self, experience: Experience) -> None:
        """Update Q-value for state-action pair."""
        state_key = self._state_to_key(experience.state)

        if state_key not in self._q_values:
            self._q_values[state_key] = {}

        current_q = self._q_values[state_key].get(experience.action, 0.0)

        # Calculate target (Q-learning update)
        if experience.next_state:
            next_key = self._state_to_key(experience.next_state)
            next_values = self._q_values.get(next_key, {})
            max_next_q = max(next_values.values()) if next_values else 0.0
        else:
            max_next_q = 0.0

        target = experience.reward + self.discount_factor * max_next_q

        # Update
        new_q = current_q + self.learning_rate * (target - current_q)
        self._q_values[state_key][experience.action] = new_q

    def _update_preferences(self, experience: Experience) -> None:
        """Update action preferences (policy gradient style)."""
        current = self._preferences.get(experience.action, 0.0)

        # Simple gradient: increase preference for positive experiences
        if experience.is_positive:
            update = self.learning_rate * experience.reward
        else:
            update = -self.learning_rate * abs(experience.reward)

        self._preferences[experience.action] = current + update

    def train_from_buffer(
        self,
        batch_size: int = 32,
        iterations: int = 10,
    ) -> Dict:
        """
        Train policy from experience buffer.

        Args:
            batch_size: Batch size for training
            iterations: Number of training iterations

        Returns:
            Training statistics
        """
        if self.buffer.size < batch_size:
            return {"status": "insufficient_data", "buffer_size": self.buffer.size}

        total_updates = 0
        avg_reward = 0.0

        for _ in range(iterations):
            batch = self.buffer.sample(batch_size)

            for experience in batch.experiences:
                self._update_q_value(experience)
                avg_reward += experience.reward
                total_updates += 1

        return {
            "status": "success",
            "iterations": iterations,
            "total_updates": total_updates,
            "avg_reward": avg_reward / total_updates if total_updates > 0 else 0,
        }

    def clone_from_demonstrations(
        self,
        demonstrations: List[Experience],
    ) -> Dict:
        """
        Learn from expert demonstrations (behavior cloning).

        Args:
            demonstrations: Expert experiences

        Returns:
            Cloning statistics
        """
        for demo in demonstrations:
            # Treat demonstrations as positive experiences
            demo.reward = max(demo.reward, 1.0)
            demo.success = True

            # Add to buffer with high priority
            self.buffer.add(demo, priority=2.0)

            # Update values
            self._update_q_value(demo)
            self._update_preferences(demo)

        return {
            "cloned": len(demonstrations),
            "buffer_size": self.buffer.size,
        }

    def _state_to_key(self, state: Dict[str, Any]) -> str:
        """Convert state to hashable key."""
        # Simple key based on sorted items
        items = sorted([(k, str(v)[:20]) for k, v in state.items()])
        return str(items)

    def _get_q_values(
        self,
        state_key: str,
        actions: List[str],
    ) -> Dict[str, float]:
        """Get Q-values for state-action pairs."""
        state_q = self._q_values.get(state_key, {})

        # Initialize missing actions with preference
        result = {}
        for action in actions:
            if action in state_q:
                result[action] = state_q[action]
            else:
                result[action] = self._preferences.get(action, 0.0)

        return result

    def decay_exploration(self, decay_rate: float = 0.995) -> None:
        """Decay exploration rate."""
        self.epsilon = max(0.01, self.epsilon * decay_rate)
        self.metrics.exploration_rate = self.epsilon

    def get_policy_stats(self) -> Dict:
        """Get policy statistics."""
        return {
            "epsilon": self.epsilon,
            "num_states": len(self._q_values),
            "num_preferences": len(self._preferences),
            "buffer_size": self.buffer.size,
            "metrics": self.metrics.get_summary(),
        }
