"""
Unit tests for LLM cost control.

Covers the CostTracker budget guard and the token estimation helper.
"""

import pytest

from core.services.llm.cost_control import CostTracker, estimate_tokens
from core.services.llm.exceptions import BudgetExceededError


class TestCostTracker:
    """Tests for CostTracker."""

    def test_track_tokens_within_budget(self):
        """Test tracking tokens within budget."""
        tracker = CostTracker(max_tokens=100)

        tracker.track_tokens(50)
        assert tracker.tokens_used == 50

        tracker.track_tokens(30)
        assert tracker.tokens_used == 80

    def test_track_tokens_exceeds_budget(self):
        """Test that exceeding budget raises error."""
        tracker = CostTracker(max_tokens=100)

        tracker.track_tokens(50)

        with pytest.raises(BudgetExceededError):
            tracker.track_tokens(60)  # Would exceed 100

    def test_track_tokens_no_limit(self):
        """Test tracking without limit."""
        tracker = CostTracker(max_tokens=None)

        tracker.track_tokens(1000)
        tracker.track_tokens(5000)

        assert tracker.tokens_used == 6000  # No error

    def test_get_usage(self):
        """Test getting usage statistics."""
        tracker = CostTracker(max_tokens=100)
        tracker.track_tokens(30)

        usage = tracker.get_usage()

        assert usage["tokens_used"] == 30
        assert usage["max_tokens"] == 100
        assert usage["remaining"] == 70


class TestEstimateTokens:
    """Tests for token estimation."""

    def test_estimate_tokens(self):
        """Test token estimation."""
        assert estimate_tokens("") == 0
        assert estimate_tokens("test") == 1  # 4 chars = 1 token
        assert estimate_tokens("test test") == 2  # 9 chars = 2 tokens
        # 100 chars = 13 tokens with tiktoken, ~25-33 with heuristic
        tokens = estimate_tokens("a" * 100)
        assert tokens in (13, 25)
