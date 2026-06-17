"""
Focused unit tests for the learning performance fixes.

Covers:
- ``AutoFineTuningService`` running-sum average correctness across appends
  and buffer eviction (the O(1) ``_score_sum`` must match a fresh recompute).
- ``ContinuousLearner`` O(1) experience id-index lookup used by
  ``provide_feedback`` (and its consistency with eviction / demonstrations).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from core.learning.auto_finetuning import AutoFineTuneConfig, AutoFineTuningService
from core.learning.experience_buffer import ExperienceReplay
from core.learning.learning_loop import ContinuousLearner
from core.learning.types import Experience


def _recompute_avg(service: AutoFineTuningService) -> float | None:
    """Independent O(n) recompute of the buffer average for cross-checking."""
    if not service._buffer:
        return None
    return sum(s.score for s in service._buffer) / len(service._buffer)


class TestRunningSumAverage:
    """The maintained ``_score_sum`` must equal a fresh sum at all times."""

    def _make_service(self, max_buffer_size: int) -> AutoFineTuningService:
        # score_threshold high so every sample is collected; auto_trigger off
        # so no background task is spawned during the test.
        config = AutoFineTuneConfig(
            score_threshold=1.0,
            min_samples=1,
            max_buffer_size=max_buffer_size,
            auto_trigger=False,
        )
        with patch("core.learning.auto_finetuning.get_event_bus"):
            service = AutoFineTuningService(config=config)
        service._running = True
        return service

    async def _feed(self, service: AutoFineTuningService, score: float) -> None:
        await service._on_evaluation_completed(
            {
                "score": score,
                "query": "q",
                "response": "r",
                "intent": "test",
            }
        )

    async def test_running_sum_matches_on_append(self) -> None:
        service = self._make_service(max_buffer_size=100)

        scores = [0.1, 0.25, 0.4, 0.05, 0.3]
        for s in scores:
            await self._feed(service, s)

        assert len(service._buffer) == len(scores)
        assert service._score_sum == pytest.approx(sum(scores))
        assert service._score_sum == pytest.approx(
            _recompute_avg(service) * len(scores)
        )

    async def test_running_sum_consistent_after_eviction(self) -> None:
        # Cap of 3 forces eviction of the oldest samples once exceeded.
        service = self._make_service(max_buffer_size=3)

        scores = [0.1, 0.2, 0.3, 0.4, 0.5]
        for s in scores:
            await self._feed(service, s)

        # Only the last 3 samples survive.
        assert len(service._buffer) == 3
        surviving = scores[-3:]
        assert [s.score for s in service._buffer] == pytest.approx(surviving)

        # The running sum must equal a fresh recompute over the survivors,
        # i.e. the evicted scores were subtracted correctly.
        assert service._score_sum == pytest.approx(sum(surviving))
        recomputed = _recompute_avg(service)
        assert service._score_sum / len(service._buffer) == pytest.approx(recomputed)

    async def test_get_stats_avg_uses_running_sum(self) -> None:
        service = self._make_service(max_buffer_size=10)
        for s in [0.2, 0.4, 0.6]:
            await self._feed(service, s)

        stats = service.get_stats()
        assert stats["avg_buffer_score"] == pytest.approx(0.4)
        assert stats["avg_buffer_score"] == pytest.approx(_recompute_avg(service))

    async def test_running_sum_resets_to_zero_on_empty(self) -> None:
        service = self._make_service(max_buffer_size=10)
        for s in [0.3, 0.3]:
            await self._feed(service, s)
        assert service._score_sum == pytest.approx(0.6)

        # Manually mirror trigger_finetuning's buffer clear semantics.
        async with service._lock:
            service._buffer = []
            service._score_sum = 0.0

        assert service.get_stats()["avg_buffer_score"] is None
        # Feeding again starts the sum cleanly from zero.
        await self._feed(service, 0.5)
        assert service._score_sum == pytest.approx(0.5)


class TestExperienceIdIndex:
    """``provide_feedback`` resolves experiences via the O(1) id index."""

    def test_index_populated_on_record(self) -> None:
        learner = ContinuousLearner()
        exp = learner.record_experience(
            state={"task": "search"},
            action="execute",
            outcome="ok",
            success=True,
        )

        assert exp.id in learner._id_index
        assert learner._id_index[exp.id] is exp

    def test_provide_feedback_updates_reward_model(self) -> None:
        learner = ContinuousLearner()
        exp = learner.record_experience(
            state={},
            action="search",
            outcome="ok",
            success=True,
        )

        before = learner.reward_model.get_action_value("search")
        learner.provide_feedback(exp.id, human_reward=1.0)
        after = learner.reward_model.get_action_value("search")

        assert after != before

    def test_provide_feedback_uses_index_not_buffer_scan(self) -> None:
        learner = ContinuousLearner()
        exp = learner.record_experience({}, "search", "ok", True)

        # If the lookup used the buffer scan, removing the buffer would break
        # it. The index must resolve the experience without touching it.
        with patch.object(
            learner.buffer,
            "_get_all_experiences",
            side_effect=AssertionError("provide_feedback must not scan the buffer"),
        ):
            learner.provide_feedback(exp.id, human_reward=0.5)

        assert learner.reward_model.get_action_value("search") != 0

    def test_provide_feedback_unknown_id_is_noop(self) -> None:
        learner = ContinuousLearner()
        learner.record_experience({}, "search", "ok", True)

        # Must not raise for an id that was never recorded.
        learner.provide_feedback("does-not-exist", human_reward=1.0)

    def test_index_bounded_to_buffer_capacity(self) -> None:
        # Small capacity so the index eviction path is exercised.
        learner = ContinuousLearner(buffer_capacity=3)

        ids = []
        for i in range(6):
            exp = learner.record_experience({"i": i}, f"a{i}", "ok", True)
            ids.append(exp.id)

        # Index never exceeds capacity; only the newest ids are retained.
        assert len(learner._id_index) == 3
        assert ids[-1] in learner._id_index
        assert ids[0] not in learner._id_index

    def test_import_demonstrations_are_indexed(self) -> None:
        learner = ContinuousLearner()
        learner.import_demonstrations(
            [
                {"state": {"x": 1}, "action": "demo", "outcome": "good"},
            ]
        )

        # Exactly one demonstration was indexed and is feedback-resolvable.
        assert len(learner._id_index) == 1
        demo_id = next(iter(learner._id_index))
        learner.provide_feedback(demo_id, human_reward=1.0)
        assert learner.reward_model.get_action_value("demo") != 0


class TestEpisodeBufferBounded:
    """Completed episodes are retained in a bounded deque, not a raw list."""

    def test_episodes_capped_to_episode_capacity(self) -> None:
        buffer = ExperienceReplay(capacity=100, episode_capacity=3)

        for i in range(5):
            buffer.start_episode({"i": i})
            buffer.add(Experience(action=f"a{i}"))
            buffer.end_episode(success=True)

        # Only the most recent ``episode_capacity`` episodes survive.
        assert len(buffer._episodes) == 3
        contexts = [ep.context["i"] for ep in buffer._episodes]
        assert contexts == [2, 3, 4]
        assert buffer.get_stats()["episodes"] == 3

    def test_sample_episodes_returns_list(self) -> None:
        buffer = ExperienceReplay(capacity=100, episode_capacity=10)
        for _ in range(4):
            buffer.start_episode()
            buffer.end_episode()

        sampled = buffer.sample_episodes(2)
        assert isinstance(sampled, list)
        assert len(sampled) == 2

        # Asking for more than available returns all (still a list).
        all_eps = buffer.sample_episodes(99)
        assert isinstance(all_eps, list)
        assert len(all_eps) == 4

    def test_priorities_attribute_removed(self) -> None:
        # The dead ``_priorities`` list must be gone (Fix 3).
        buffer = ExperienceReplay()
        assert not hasattr(buffer, "_priorities")
        # clear() must still work without it.
        buffer.start_episode()
        buffer.add(Experience(action="x"))
        buffer.end_episode()
        buffer.clear()
        assert len(buffer._episodes) == 0


class TestGetBestActionsHeapq:
    """``get_best_actions`` returns the correct top-k via heapq.nlargest."""

    def test_returns_top_k_sorted_desc(self) -> None:
        learner = ContinuousLearner()
        learner.reward_model.update_from_feedback(Experience(action="a"), 0.1)
        learner.reward_model.update_from_feedback(Experience(action="b"), 0.9)
        learner.reward_model.update_from_feedback(Experience(action="c"), 0.5)

        top = learner.get_best_actions({}, ["a", "b", "c"], top_k=2)

        assert [action for action, _ in top] == ["b", "c"]
        # Values are sorted descending (nlargest contract).
        assert top[0][1] >= top[1][1]

    def test_top_k_larger_than_actions(self) -> None:
        learner = ContinuousLearner()
        top = learner.get_best_actions({}, ["only"], top_k=5)
        assert len(top) == 1
        assert top[0][0] == "only"
