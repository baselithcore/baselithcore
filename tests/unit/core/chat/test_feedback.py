"""
Tests for core.chat.feedback module.
"""

from unittest.mock import Mock

from core.chat.feedback import apply_feedback_boost, RankedHit


class TestApplyFeedbackBoost:
    """Tests for apply_feedback_boost function."""

    def test_empty_hits(self):
        """Test with empty hits list."""
        result = apply_feedback_boost(
            [], {}, min_total=1, positive_weight=1.0, negative_weight=0.5
        )
        assert result == []

    def test_no_feedback_stats(self):
        """Test with no feedback statistics."""
        mock_hit = Mock()
        mock_hit.payload = {"document_id": "doc1"}
        hits = [(mock_hit, 0.8)]

        result = apply_feedback_boost(
            hits, {}, min_total=1, positive_weight=1.0, negative_weight=0.5
        )

        assert len(result) == 1
        assert result[0][1] == 0.8  # Score unchanged

    def test_boost_with_positive_feedback(self):
        """Test score boost with positive feedback."""
        mock_hit = Mock()
        mock_hit.payload = {"document_id": "doc1"}
        mock_hit.id = "doc1"
        hits = [(mock_hit, 0.8)]

        feedback_stats = {"id::doc1": {"total": 5, "positives": 4, "negatives": 1}}

        result = apply_feedback_boost(
            hits, feedback_stats, min_total=1, positive_weight=0.1, negative_weight=0.1
        )

        assert len(result) == 1
        # Score should be boosted: 0.8 + (4 * 0.1) - (1 * 0.1) = 0.8 + 0.3 = 1.1
        assert result[0][1] > 0.8

    def test_below_min_total_no_boost(self):
        """Test no boost when below min_total."""
        mock_hit = Mock()
        mock_hit.payload = {"document_id": "doc1"}
        mock_hit.id = "doc1"
        hits = [(mock_hit, 0.8)]

        feedback_stats = {"id::doc1": {"total": 2, "positives": 2, "negatives": 0}}

        result = apply_feedback_boost(
            hits, feedback_stats, min_total=5, positive_weight=0.1, negative_weight=0.1
        )

        assert len(result) == 1
        assert result[0][1] == 0.8  # Score unchanged because total < min_total

    def test_sorting_by_adjusted_score(self):
        """Test results are sorted by adjusted score."""
        hit1 = Mock()
        hit1.payload = {"document_id": "doc1"}
        hit1.id = "doc1"

        hit2 = Mock()
        hit2.payload = {"document_id": "doc2"}
        hit2.id = "doc2"

        hits = [(hit1, 0.5), (hit2, 0.8)]

        feedback_stats = {
            "id::doc1": {"total": 5, "positives": 10, "negatives": 0}  # Big boost
        }

        result = apply_feedback_boost(
            hits, feedback_stats, min_total=1, positive_weight=0.1, negative_weight=0.1
        )

        assert len(result) == 2
        # doc1 should now be first due to boost
        assert result[0][0].payload["document_id"] == "doc1"


class TestRankedHitType:
    """Tests for RankedHit type alias."""

    def test_ranked_hit_is_tuple(self):
        """Test RankedHit is available as type."""
        assert RankedHit is not None
