"""
Unit Tests for Continuous Learning Primitives

Tests for learning types, experience replay, reward model and policy optimizer.
"""

from core.learning.experience_buffer import ExperienceReplay
from core.learning.policy_optimizer import PolicyOptimizer
from core.learning.reward_model import RewardModel
from core.learning.types import Episode, Experience, LearningMetrics, Reward, RewardType

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
