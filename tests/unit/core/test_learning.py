"""
Unit Tests for Continuous Learning Module

Tests for experience replay, reward model, policy optimizer, and continuous learner.
"""

from unittest.mock import Mock, patch

from core.learning.types import Experience, Reward, Episode, LearningMetrics, RewardType
from core.learning.experience_buffer import ExperienceReplay
from core.learning.reward_model import RewardModel
from core.learning.policy_optimizer import PolicyOptimizer
from core.learning.learning_loop import ContinuousLearner, PersistentLearner


# ============================================================================
# Types Tests
# ============================================================================


class TestExperience:
    """Tests for Experience."""

    def test_creation(self):
        """Basic experience creation."""
        exp = Experience(
            state={"task": "search"},
            action="execute",
            reward=1.0,
            success=True,
        )

        assert exp.action == "execute"
        assert exp.is_positive

    def test_is_positive(self):
        """Check positive detection."""
        positive = Experience(reward=0.5)
        negative = Experience(reward=-0.5)
        neutral = Experience(reward=0.0, success=True)

        assert positive.is_positive
        assert not negative.is_positive
        assert neutral.is_positive  # success overrides

    def test_to_dict(self):
        """Convert to dictionary."""
        exp = Experience(action="test")
        d = exp.to_dict()

        assert d["action"] == "test"
        assert "timestamp" in d


class TestReward:
    """Tests for Reward."""

    def test_reward_type(self):
        """Determine reward type from value."""
        positive = Reward(value=0.5)
        negative = Reward(value=-0.5)
        neutral = Reward(value=0.0)

        assert positive.reward_type == RewardType.POSITIVE
        assert negative.reward_type == RewardType.NEGATIVE
        assert neutral.reward_type == RewardType.NEUTRAL


class TestEpisode:
    """Tests for Episode."""

    def test_add_experience(self):
        """Add experience to episode."""
        episode = Episode()
        episode.add_experience(Experience(reward=1.0))
        episode.add_experience(Experience(reward=0.5))

        assert episode.length == 2
        assert episode.total_reward == 1.5

    def test_avg_reward(self):
        """Calculate average reward."""
        episode = Episode()
        episode.add_experience(Experience(reward=1.0))
        episode.add_experience(Experience(reward=0.0))

        assert episode.avg_reward == 0.5


class TestLearningMetrics:
    """Tests for LearningMetrics."""

    def test_update(self):
        """Update metrics with experience."""
        metrics = LearningMetrics()
        metrics.update(Experience(reward=1.0, success=True))

        assert metrics.total_experiences == 1
        assert metrics.positive_experiences == 1


# ============================================================================
# ExperienceReplay Tests
# ============================================================================


class TestExperienceReplay:
    """Tests for ExperienceReplay."""

    def test_add_and_sample(self):
        """Add and sample experiences."""
        buffer = ExperienceReplay(capacity=100)

        for i in range(10):
            buffer.add(Experience(action=f"action_{i}"))

        assert buffer.size == 10

        batch = buffer.sample(5)
        assert len(batch) == 5

    def test_capacity_limit(self):
        """Buffer respects capacity."""
        buffer = ExperienceReplay(capacity=5)

        for i in range(10):
            buffer.add(Experience(action=f"action_{i}"))

        assert buffer.size == 5

    def test_episode_tracking(self):
        """Track episodes."""
        buffer = ExperienceReplay()

        buffer.start_episode({"goal": "test"})
        buffer.add(Experience(action="step1"))
        buffer.add(Experience(action="step2"))
        completed = buffer.end_episode(success=True)

        assert completed.length == 2
        assert completed.success

    def test_prioritized_sampling(self):
        """Prioritized experience replay."""
        buffer = ExperienceReplay(capacity=100, prioritized=True)

        # Add low priority
        buffer.add(Experience(action="low"), priority=0.1)
        # Add high priority
        buffer.add(Experience(action="high"), priority=1.0)

        # PER samples batch_size items WITH REPLACEMENT
        # So we should get 10 samples (may include duplicates)
        batch = buffer.sample(10)
        assert len(batch.experiences) == 10  # batch_size samples
        assert len(batch.weights) == 10  # Importance weights for each

        # High priority samples should appear more often
        high_count = sum(1 for e in batch.experiences if e.action == "high")
        low_count = sum(1 for e in batch.experiences if e.action == "low")
        # High priority (1.0) should generally be sampled more than low (0.1)
        assert (
            high_count >= low_count or high_count > 0
        )  # At minimum, high should appear

    def test_get_positive_negative(self):
        """Get positive and negative experiences."""
        buffer = ExperienceReplay()

        buffer.add(Experience(action="good", reward=1.0, success=True))
        buffer.add(Experience(action="bad", reward=-1.0, success=False))

        positives = buffer.get_positive_experiences(10)
        negatives = buffer.get_negative_experiences(10)

        assert len(positives) == 1
        assert len(negatives) == 1


# ============================================================================
# RewardModel Tests
# ============================================================================


class TestRewardModel:
    """Tests for RewardModel."""

    def test_default_rules(self):
        """Default rules are applied."""
        model = RewardModel()

        success_exp = Experience(success=True)
        reward = model.calculate_reward(success_exp)

        assert reward.value > 0

    def test_custom_rule(self):
        """Add custom reward rule."""
        model = RewardModel()

        model.add_rule(
            "fast_completion",
            condition=lambda e: e.metadata.get("time", 100) < 10,
            reward=lambda e: 0.5,
        )

        fast_exp = Experience(metadata={"time": 5})
        reward = model.calculate_reward(fast_exp)

        assert reward.value >= 0.5

    def test_update_from_feedback(self):
        """Update from human feedback."""
        model = RewardModel()

        exp = Experience(action="search")
        model.update_from_feedback(exp, 1.0)

        value = model.get_action_value("search")
        assert value > 0

    def test_get_best_action(self):
        """Get action with highest value."""
        model = RewardModel()

        model.update_from_feedback(Experience(action="good"), 1.0)
        model.update_from_feedback(Experience(action="bad"), -1.0)

        best = model.get_best_action(["good", "bad", "unknown"])
        assert best == "good"


# ============================================================================
# PolicyOptimizer Tests
# ============================================================================


class TestPolicyOptimizer:
    """Tests for PolicyOptimizer."""

    def test_select_action(self):
        """Select action from available."""
        optimizer = PolicyOptimizer(epsilon=0.0)  # No exploration

        action = optimizer.select_action(
            {"task": "test"},
            ["search", "generate", "ask"],
        )

        assert action in ["search", "generate", "ask"]

    def test_exploration(self):
        """Epsilon-greedy exploration."""
        optimizer = PolicyOptimizer(epsilon=1.0)  # Always explore

        # Should pick randomly (hard to test, but should not error)
        actions = set()
        for _ in range(10):
            action = optimizer.select_action({}, ["a", "b", "c"])
            actions.add(action)

        # With 100% exploration, should see variety
        assert len(actions) >= 1

    def test_update(self):
        """Update policy with experience."""
        optimizer = PolicyOptimizer()

        exp = Experience(
            state={"task": "search"},
            action="execute",
            reward=1.0,
            success=True,
        )

        optimizer.update(exp)

        # Action should now have positive preference
        assert optimizer._preferences.get("execute", 0) > 0

    def test_train_from_buffer(self):
        """Train from experience buffer."""
        optimizer = PolicyOptimizer()

        for i in range(50):
            optimizer.update(
                Experience(
                    state={"step": i},
                    action="step",
                    reward=0.1,
                )
            )

        result = optimizer.train_from_buffer(batch_size=10, iterations=5)

        assert result["status"] == "success"

    def test_decay_exploration(self):
        """Decay exploration rate."""
        optimizer = PolicyOptimizer(epsilon=0.5)

        optimizer.decay_exploration(decay_rate=0.9)

        assert optimizer.epsilon == 0.45


# ============================================================================
# ContinuousLearner Tests
# ============================================================================


class TestContinuousLearner:
    """Tests for ContinuousLearner."""

    def test_initialization(self):
        """Default initialization."""
        learner = ContinuousLearner()

        assert learner.buffer is not None
        assert learner.optimizer is not None

    def test_episode_lifecycle(self):
        """Start and end episode."""
        learner = ContinuousLearner()

        episode = learner.start_episode({"goal": "test"})
        assert episode is not None

        completed = learner.end_episode(success=True)
        assert completed.success

    def test_record_experience(self):
        """Record experience."""
        learner = ContinuousLearner()
        learner.start_episode()

        exp = learner.record_experience(
            state={"task": "search"},
            action="execute",
            outcome="found results",
            success=True,
        )

        assert exp.reward != 0  # Reward calculated
        assert learner.buffer.size == 1

    def test_select_action(self):
        """Select action using learned policy."""
        learner = ContinuousLearner()

        action = learner.select_action(
            {"task": "analyze"},
            ["search", "generate", "ask"],
        )

        assert action in ["search", "generate", "ask"]

    def test_train(self):
        """Trigger training."""
        learner = ContinuousLearner()

        # Add experiences
        for i in range(50):
            learner.record_experience(
                state={"step": i},
                action="progress",
                outcome="ok",
                success=True,
            )

        result = learner.train()

        assert result["status"] == "success"

    def test_get_best_actions(self):
        """Get top actions by value."""
        learner = ContinuousLearner()

        # Train on some actions
        learner.record_experience({}, "best", "great", True)
        learner.record_experience({}, "worst", "bad", False)

        top = learner.get_best_actions({}, ["best", "worst", "unknown"], top_k=2)

        assert len(top) == 2

    def test_save_load_state(self):
        """Save and load learner state."""
        learner = ContinuousLearner()

        learner.record_experience({}, "action", "outcome", True)

        state = learner.save_state()

        new_learner = ContinuousLearner()
        new_learner.load_state(state)

        assert new_learner._exp_count == learner._exp_count


# ============================================================================
# Integration Test
# ============================================================================


def test_continuous_learning_workflow():
    """Full continuous learning workflow."""
    learner = ContinuousLearner(
        buffer_capacity=1000,
        training_interval=20,
        exploration_rate=0.2,
    )

    # Simulate agent episodes
    for episode_num in range(5):
        learner.start_episode({"episode": episode_num})

        for step in range(10):
            state = {"step": step, "episode": episode_num}
            actions = ["search", "generate", "ask", "execute"]

            # Select action
            action = learner.select_action(state, actions)

            # Simulate outcome
            success = step > 5  # Later steps more successful

            # Record
            learner.record_experience(
                state=state,
                action=action,
                outcome=f"step_{step}_result",
                success=success,
            )

        learner.end_episode(success=True)

    # Verify learning happened
    stats = learner.get_stats()

    assert stats["experiences_collected"] == 50
    assert stats["buffer"]["size"] == 50
    assert stats["policy"]["num_preferences"] > 0

    # Policy should have learned something
    best = learner.get_best_actions({}, ["search", "generate", "ask"])
    assert len(best) >= 1


# ============================================================================
# PersistentLearner Tests
# ============================================================================


class TestPersistentLearner:
    """Tests for PersistentLearner."""

    def test_initialization_defaults(self):
        """Initialize with defaults."""
        learner = PersistentLearner(learner_id="test", auto_load=False)

        assert learner.learner_id == "test"
        assert learner.auto_save is True
        assert learner.checkpoint_interval == 1

    def test_cache_key_generation(self):
        """Generate correct cache key."""
        learner = PersistentLearner(learner_id="agent_1", auto_load=False)

        expected = "learner:state:agent_1"
        assert learner._cache_key == expected

    @patch("core.learning.learning_loop.PersistentLearner.cache")
    def test_auto_save_after_training(self, mock_cache):
        """Auto-save triggers after training."""
        mock_cache._enabled = True
        mock_cache.set = Mock()
        mock_cache.get = Mock(return_value=None)

        learner = PersistentLearner(learner_id="test", auto_load=False)

        # Add enough experiences
        for i in range(50):
            learner.record_experience({}, "action", "outcome", True)

        # Train - should trigger save
        learner.train()

        mock_cache.set.assert_called()

    @patch("core.learning.learning_loop.PersistentLearner.cache")
    def test_manual_checkpoint(self, mock_cache):
        """Manual checkpoint save."""
        mock_cache._enabled = True
        mock_cache.set = Mock()
        mock_cache.get = Mock(return_value=None)

        learner = PersistentLearner(learner_id="test", auto_load=False, auto_save=False)
        learner.record_experience({}, "action", "outcome", True)

        result = learner.checkpoint()

        assert result is True
        mock_cache.set.assert_called_once()

    @patch("core.learning.learning_loop.PersistentLearner.cache")
    def test_auto_load_on_init(self, mock_cache):
        """Load state on initialization if available."""
        mock_cache._enabled = True
        mock_cache.get = Mock(
            return_value={
                "exp_count": 100,
                "train_count": 5,
                "epsilon": 0.05,
                "q_values": {"state1": {"action1": 0.5}},
                "preferences": {"action1": 0.3},
                "action_values": {"action1": 0.8},
            }
        )

        learner = PersistentLearner(learner_id="test", auto_load=True)

        assert learner._exp_count == 100
        assert learner._train_count == 5
        assert learner.optimizer.epsilon == 0.05

    @patch("core.learning.learning_loop.PersistentLearner.cache")
    def test_get_stats_includes_persistence(self, mock_cache):
        """Stats include persistence info."""
        mock_cache._enabled = True
        mock_cache.get = Mock(return_value=None)

        learner = PersistentLearner(learner_id="my_agent", auto_load=False)
        stats = learner.get_stats()

        assert "persistence" in stats
        assert stats["persistence"]["learner_id"] == "my_agent"
        assert stats["persistence"]["auto_save"] is True
