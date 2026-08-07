"""Routing scoreboard: outcome-fed model choice with confidence guards."""

import pytest

from core.models.routing import Complexity, RoutingPolicy, TaskCategory
from core.models.routing_stats import LearnedModelRouter, RoutingScoreboard


def _feed(board, category, model, *, attempts, successes):
    for i in range(attempts):
        board.record(category, model, success=i < successes)


class TestRoutingScoreboard:
    def test_rejects_nonsense_configuration(self):
        with pytest.raises(ValueError):
            RoutingScoreboard(min_samples=0)
        with pytest.raises(ValueError):
            RoutingScoreboard(margin=1.5)

    def test_no_preference_below_the_sample_floor(self):
        board = RoutingScoreboard(min_samples=20)
        _feed(board, TaskCategory.CLASSIFICATION, "claude-haiku-4-5", attempts=5, successes=5)
        _feed(board, TaskCategory.CLASSIFICATION, "claude-opus-4-8", attempts=25, successes=20)
        # One lucky streak must not move traffic.
        assert board.prefer(TaskCategory.CLASSIFICATION, "claude-opus-4-8") is None

    def test_prefers_a_clearly_better_model(self):
        board = RoutingScoreboard(min_samples=10, margin=0.05)
        _feed(board, TaskCategory.SUMMARIZATION, "claude-opus-4-8", attempts=20, successes=14)
        _feed(board, TaskCategory.SUMMARIZATION, "claude-haiku-4-5", attempts=20, successes=20)
        assert (
            board.prefer(TaskCategory.SUMMARIZATION, "claude-opus-4-8")
            == "claude-haiku-4-5"
        )

    def test_a_margin_sized_gap_is_treated_as_noise(self):
        board = RoutingScoreboard(min_samples=10, margin=0.2)
        _feed(board, TaskCategory.EXECUTION, "claude-sonnet-4-6", attempts=20, successes=16)
        _feed(board, TaskCategory.EXECUTION, "claude-haiku-4-5", attempts=20, successes=17)
        assert board.prefer(TaskCategory.EXECUTION, "claude-sonnet-4-6") is None

    def test_allowed_set_bounds_the_challenger(self):
        board = RoutingScoreboard(min_samples=5, margin=0.05)
        _feed(board, TaskCategory.PLANNING, "claude-opus-4-8", attempts=10, successes=5)
        _feed(board, TaskCategory.PLANNING, "some-unvetted-model", attempts=10, successes=10)
        assert (
            board.prefer(
                TaskCategory.PLANNING, "claude-opus-4-8", allowed={"claude-haiku-4-5"}
            )
            is None
        )

    def test_categories_do_not_leak_into_each_other(self):
        board = RoutingScoreboard(min_samples=5)
        _feed(board, TaskCategory.CLASSIFICATION, "claude-haiku-4-5", attempts=10, successes=10)
        assert board.candidates(TaskCategory.PLANNING) == {}

    def test_snapshot_reports_rates_and_costs(self):
        board = RoutingScoreboard()
        board.record(
            TaskCategory.EXECUTION,
            "claude-haiku-4-5",
            success=True,
            cost_usd=0.002,
            latency_ms=300,
        )
        snap = board.snapshot()["execution:claude-haiku-4-5"]
        assert snap["attempts"] == 1
        assert snap["success_rate"] == 1.0
        assert snap["avg_cost_usd"] == 0.002


class TestLearnedModelRouter:
    def test_without_a_scoreboard_it_is_the_static_router(self):
        router = LearnedModelRouter()
        decision = router.select(TaskCategory.PLANNING)
        assert decision.rule == "primary"
        assert decision.model_id == "claude-opus-4-8"

    def test_an_empty_scoreboard_changes_nothing(self):
        router = LearnedModelRouter(scoreboard=RoutingScoreboard())
        assert router.select(TaskCategory.PLANNING).rule == "primary"

    def test_confident_evidence_overrides_the_policy(self):
        board = RoutingScoreboard(min_samples=10, margin=0.05)
        _feed(board, TaskCategory.SUMMARIZATION, "claude-haiku-4-5", attempts=20, successes=12)
        _feed(board, TaskCategory.SUMMARIZATION, "claude-sonnet-4-6", attempts=20, successes=20)
        router = LearnedModelRouter(scoreboard=board)

        decision = router.select(TaskCategory.SUMMARIZATION)
        assert decision.model_id == "claude-sonnet-4-6"
        # The override is auditable: a learned pick never masquerades as policy.
        assert decision.rule == "learned_override"

    def test_complexity_upgrade_still_applies_first(self):
        router = LearnedModelRouter(policy=RoutingPolicy(), scoreboard=RoutingScoreboard())
        decision = router.select(TaskCategory.EXECUTION, Complexity.COMPLEX)
        assert decision.rule == "complexity_upgrade"
