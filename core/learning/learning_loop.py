"""
Continuous Learning Orchestration.

The primary engine for agentic self-improvement. Coordinates the lifecycle
of collecting experiences, calculating rewards, and periodically training
the agent's policy to optimize its decision-making over time.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.optimization.caching import RedisCache

from .types import Experience, Episode, LearningPhase
from .experience_buffer import ExperienceReplay
from .reward_model import RewardModel
from .policy_optimizer import PolicyOptimizer

try:
    from core.events import get_event_bus, EventNames

    _HAS_EVENT_BUS = True
except ImportError:
    _HAS_EVENT_BUS = False

logger = get_logger(__name__)


class ContinuousLearner:
    """
    Controller for the continuous improvement cycle.

    Integrates experience collection and reward modeling to drive policy
    optimization. Supports both live training and periodic batch updates,
    ensuring that the agent consistently adapts to new information and
    user feedback.
    """

    def __init__(
        self,
        buffer_capacity: int = 10000,
        training_interval: int = 100,
        batch_size: int = 32,
        exploration_rate: float = 0.1,
    ):
        """
        Initialize continuous learner.

        Args:
            buffer_capacity: Experience buffer capacity
            training_interval: Train every N experiences
            batch_size: Training batch size
            exploration_rate: Initial exploration rate
        """
        self.buffer = ExperienceReplay(capacity=buffer_capacity, prioritized=True)
        self.reward_model = RewardModel()
        self.optimizer = PolicyOptimizer(
            experience_buffer=self.buffer,
            reward_model=self.reward_model,
            epsilon=exploration_rate,
        )

        self.training_interval = training_interval
        self.batch_size = batch_size
        self.phase = LearningPhase.COLLECTING

        # Counters
        self._exp_count = 0
        self._train_count = 0
        self._current_episode: Optional[Episode] = None

    def start_episode(self, context: Optional[Dict] = None) -> Episode:
        """
        Start a new learning episode.

        Args:
            context: Episode context

        Returns:
            New episode
        """
        self._current_episode = self.buffer.start_episode(context)
        logger.debug(f"Started episode {self._current_episode.id}")
        return self._current_episode

    def end_episode(self, success: bool = False) -> Optional[Episode]:
        """
        End current episode.

        Args:
            success: Whether episode was successful

        Returns:
            Completed episode
        """
        episode = self.buffer.end_episode(success)

        if episode:
            logger.debug(
                f"Ended episode {episode.id}: success={success}, "
                f"length={episode.length}, reward={episode.total_reward:.2f}"
            )

        self._current_episode = None
        return episode

    def select_action(
        self,
        state: Dict[str, Any],
        available_actions: List[str],
    ) -> str:
        """
        Select action using learned policy.

        Args:
            state: Current state
            available_actions: Available actions

        Returns:
            Selected action
        """
        return self.optimizer.select_action(state, available_actions)

    def record_experience(
        self,
        state: Dict[str, Any],
        action: str,
        outcome: str,
        success: bool = False,
        next_state: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
    ) -> Experience:
        """
        Record an experience.

        Args:
            state: State when action was taken
            action: Action taken
            outcome: Outcome description
            success: Whether action was successful
            next_state: Resulting state
            metadata: Additional metadata

        Returns:
            Recorded experience
        """
        experience = Experience(
            state=state,
            action=action,
            outcome=outcome,
            success=success,
            next_state=next_state,
            metadata=metadata or {},
        )

        # Calculate reward
        reward = self.reward_model.calculate_reward(experience)
        experience.reward = reward.value
        experience.reward_type = reward.reward_type

        # Add to buffer and update
        self.optimizer.update(experience)
        self._exp_count += 1

        # Emit experience recorded event
        if _HAS_EVENT_BUS:
            get_event_bus().emit_sync(
                EventNames.EXPERIENCE_RECORDED,
                {
                    "action": action,
                    "reward": experience.reward,
                    "success": success,
                    "total_experiences": self._exp_count,
                },
            )

        # Check for training trigger
        if self._exp_count % self.training_interval == 0:
            self._maybe_train()

        return experience

    def provide_feedback(
        self,
        experience_id: str,
        human_reward: float,
    ) -> None:
        """
        Provide human feedback for an experience.

        Args:
            experience_id: ID of the experience
            human_reward: Human-provided reward
        """
        # Find experience (works for both prioritized and plain modes)
        for exp in self.buffer._get_all_experiences():
            if exp.id == experience_id:
                self.reward_model.update_from_feedback(exp, human_reward)
                break

    def train(self, iterations: int = 10) -> Dict:
        """
        Trigger training.

        Args:
            iterations: Number of training iterations

        Returns:
            Training statistics
        """
        self.phase = LearningPhase.TRAINING

        result = self.optimizer.train_from_buffer(
            batch_size=self.batch_size,
            iterations=iterations,
        )

        self._train_count += 1
        self.phase = LearningPhase.COLLECTING

        # Decay exploration
        self.optimizer.decay_exploration()

        logger.info(f"Training complete: {result}")
        return result

    def _maybe_train(self) -> None:
        """Check if training should happen."""
        if self.buffer.size >= self.batch_size * 2:
            self.train()

    def import_demonstrations(
        self,
        demonstrations: List[Dict],
    ) -> Dict:
        """
        Import expert demonstrations.

        Args:
            demonstrations: List of demo dicts with state, action, outcome

        Returns:
            Import statistics
        """
        experiences = []
        for demo in demonstrations:
            exp = Experience(
                state=demo.get("state", {}),
                action=demo.get("action", ""),
                outcome=demo.get("outcome", ""),
                success=demo.get("success", True),
                reward=demo.get("reward", 1.0),
            )
            experiences.append(exp)

        return self.optimizer.clone_from_demonstrations(experiences)

    def get_best_actions(
        self,
        state: Dict[str, Any],
        actions: List[str],
        top_k: int = 3,
    ) -> List[tuple]:
        """
        Get top K actions by learned value.

        Args:
            state: Current state
            actions: Available actions
            top_k: Number of actions to return

        Returns:
            List of (action, value) tuples
        """
        values = []
        for action in actions:
            value = self.reward_model.get_action_value(action)
            values.append((action, value))

        values.sort(key=lambda x: x[1], reverse=True)
        return values[:top_k]

    def get_stats(self) -> Dict:
        """Get learner statistics."""
        return {
            "phase": self.phase.value,
            "experiences_collected": self._exp_count,
            "training_runs": self._train_count,
            "exploration_rate": self.optimizer.epsilon,
            "buffer": self.buffer.get_stats(),
            "reward_model": self.reward_model.get_stats(),
            "policy": self.optimizer.get_policy_stats(),
        }

    def save_state(self) -> Dict:
        """Save learner state for persistence."""
        return {
            "exp_count": self._exp_count,
            "train_count": self._train_count,
            "epsilon": self.optimizer.epsilon,
            "q_values": self.optimizer._q_values,
            "preferences": self.optimizer._preferences,
            "action_values": self.reward_model._action_values,
        }

    def load_state(self, state: Dict) -> None:
        """Load learner state from saved dict."""
        self._exp_count = state.get("exp_count", 0)
        self._train_count = state.get("train_count", 0)
        self.optimizer.epsilon = state.get("epsilon", 0.1)
        self.optimizer._q_values = state.get("q_values", {})
        self.optimizer._preferences = state.get("preferences", {})
        self.reward_model._action_values = state.get("action_values", {})


class PersistentLearner(ContinuousLearner):
    """
    ContinuousLearner with Redis-backed state persistence.

    Automatically saves state after training and loads on initialization,
    providing fault tolerance and recovery capabilities.

    Example:
        ```python
        # First run - starts fresh, saves state after training
        learner = PersistentLearner(learner_id="my_agent")
        learner.record_experience(state, action, outcome)
        learner.train()  # Auto-saves to Redis

        # After restart - loads previous state
        learner = PersistentLearner(learner_id="my_agent")
        # Continues where it left off!
        ```
    """

    DEFAULT_CHECKPOINT_KEY = "learner:state"

    def __init__(
        self,
        learner_id: str = "default",
        auto_save: bool = True,
        auto_load: bool = True,
        checkpoint_interval: int = 1,
        **kwargs,
    ):
        """
        Initialize persistent learner.

        Args:
            learner_id: Unique identifier for this learner (used as Redis key)
            auto_save: Whether to auto-save after training (default: True)
            auto_load: Whether to auto-load state on init (default: True)
            checkpoint_interval: Save every N training runs (default: 1)
            **kwargs: Additional args passed to ContinuousLearner
        """
        super().__init__(**kwargs)

        self.learner_id = learner_id
        self.auto_save = auto_save
        self.checkpoint_interval = checkpoint_interval

        # Lazy-load Redis cache
        self._cache: Optional["RedisCache"] = None

        # Auto-load previous state
        if auto_load:
            self._try_load()

    @property
    def cache(self):
        """Lazy load Redis cache."""
        if self._cache is None:
            try:
                from core.optimization.caching import RedisCache

                self._cache = RedisCache(prefix="learner")
            except ImportError:
                logger.warning("RedisCache not available, persistence disabled")
        return self._cache

    @property
    def _cache_key(self) -> str:
        """Generate cache key for this learner."""
        return f"{self.DEFAULT_CHECKPOINT_KEY}:{self.learner_id}"

    def _try_load(self) -> bool:
        """Attempt to load state from Redis."""
        if not self.cache:
            return False

        try:
            saved_state = self.cache.get(self._cache_key)
            if saved_state and isinstance(saved_state, dict):
                self.load_state(saved_state)
                logger.info(
                    f"Loaded learner state '{self.learner_id}': "
                    f"exp={self._exp_count}, trains={self._train_count}"
                )
                return True
        except Exception as e:
            logger.warning(f"Failed to load learner state: {e}")

        return False

    def _try_save(self) -> bool:
        """Attempt to save state to Redis."""
        if not self.cache:
            return False

        try:
            state = self.save_state()
            self.cache.set(self._cache_key, state)
            logger.debug(f"Saved learner state '{self.learner_id}'")
            return True
        except Exception as e:
            logger.warning(f"Failed to save learner state: {e}")
            return False

    def train(self, iterations: int = 10) -> Dict:
        """
        Train and optionally checkpoint.

        Overrides parent to add auto-save functionality.
        """
        result = super().train(iterations)

        # Auto-save if enabled and interval met
        if self.auto_save and self._train_count % self.checkpoint_interval == 0:
            self._try_save()

        return result

    def checkpoint(self) -> bool:
        """
        Manually trigger a checkpoint save.

        Returns:
            True if save succeeded, False otherwise.
        """
        return self._try_save()

    def reset_state(self) -> None:
        """Reset learner state and clear from Redis."""
        self._exp_count = 0
        self._train_count = 0
        self.optimizer.epsilon = 0.1
        self.optimizer._q_values = {}
        self.optimizer._preferences = {}
        self.reward_model._action_values = {}

        if self.cache:
            try:
                self.cache.delete(self._cache_key)
                logger.info(f"Reset learner state '{self.learner_id}'")
            except Exception as e:
                logger.warning(f"Failed to delete learner state from Redis: {e}")

    def get_stats(self) -> Dict:
        """Get learner statistics with persistence info."""
        stats = super().get_stats()
        stats["persistence"] = {
            "learner_id": self.learner_id,
            "auto_save": self.auto_save,
            "checkpoint_interval": self.checkpoint_interval,
            "cache_available": self.cache is not None and self.cache._enabled,
        }
        return stats
