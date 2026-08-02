"""
Unit Tests for the Continuous Learning Loop

Tests for ContinuousLearner and PersistentLearner.
"""

from unittest.mock import Mock, patch

from core.learning.learning_loop import ContinuousLearner, PersistentLearner

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
