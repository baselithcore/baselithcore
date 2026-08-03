"""Tests for the group fairness metrics (AI Act Art. 10(2)(f)/(g), Art. 15)."""

from __future__ import annotations

import pytest

from core.evaluation.fairness import FOUR_FIFTHS, evaluate_fairness


class TestSelectionMetrics:
    def test_equal_selection_rates_give_perfect_parity(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"], predictions=[True, False, True, False]
        )
        assert report.demographic_parity_difference == 0.0
        assert report.disparate_impact_ratio == 1.0

    def test_unequal_selection_rates_are_measured(self):
        report = evaluate_fairness(
            groups=["a", "a", "a", "a", "b", "b", "b", "b"],
            predictions=[True, True, True, True, True, False, False, False],
        )
        # a: 4/4 = 1.0, b: 1/4 = 0.25
        assert report.demographic_parity_difference == pytest.approx(0.75)
        assert report.disparate_impact_ratio == pytest.approx(0.25)

    def test_no_positive_predictions_yields_an_undefined_ratio_of_zero(self):
        report = evaluate_fairness(
            groups=["a", "b"], predictions=[False, False]
        )
        assert report.disparate_impact_ratio == 0.0

    def test_a_single_group_never_reports_a_violation(self):
        report = evaluate_fairness(groups=["a", "a"], predictions=[True, False])
        assert report.violations() == []
        assert report.passed is True


class TestLabelledMetrics:
    def test_rates_are_computed_per_group(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"],
            predictions=[True, False, True, False],
            labels=[True, True, True, False],
        )
        by_group = {g.group: g for g in report.groups}
        assert by_group["a"].true_positive_rate == pytest.approx(0.5)
        assert by_group["b"].true_positive_rate == pytest.approx(1.0)
        assert by_group["b"].accuracy == pytest.approx(1.0)

    def test_equal_opportunity_difference_uses_the_tpr_gap(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"],
            predictions=[True, False, True, False],
            labels=[True, True, True, False],
        )
        assert report.equal_opportunity_difference == pytest.approx(0.5)

    def test_equalized_odds_takes_the_worse_of_tpr_and_fpr_gaps(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"],
            predictions=[True, True, False, False],
            labels=[True, False, True, False],
        )
        # a: TPR 1.0, FPR 1.0 | b: TPR 0.0, FPR 0.0 — both gaps are 1.0.
        assert report.equalized_odds_difference == pytest.approx(1.0)

    def test_accuracy_gap_is_reported(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"],
            predictions=[True, False, True, True],
            labels=[True, False, False, False],
        )
        assert report.accuracy_difference == pytest.approx(1.0)


class TestThresholds:
    def test_four_fifths_default_is_documented_as_the_floor(self):
        report = evaluate_fairness(groups=["a"], predictions=[True])
        assert report.disparate_impact_threshold == FOUR_FIFTHS

    def test_breaching_disparate_impact_is_reported(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"],
            predictions=[True, True, True, False],
        )
        violations = report.violations()
        assert any("disparate impact" in v for v in violations)
        assert report.passed is False

    def test_thresholds_are_configurable(self):
        report = evaluate_fairness(
            groups=["a", "a", "b", "b"],
            predictions=[True, True, True, False],
            disparate_impact_threshold=0.4,
            max_difference=0.6,
        )
        assert report.passed is True

    def test_report_serializes_with_group_detail(self):
        report = evaluate_fairness(
            groups=["a", "b"], predictions=[True, False], labels=[True, True]
        )
        payload = report.to_dict()
        assert len(payload["groups"]) == 2
        assert "equalized_odds_difference" in payload
        assert "violations" in payload


class TestInputValidation:
    def test_misaligned_predictions_raise(self):
        with pytest.raises(ValueError, match="must align"):
            evaluate_fairness(groups=["a", "b"], predictions=[True])

    def test_misaligned_labels_raise(self):
        with pytest.raises(ValueError, match="must align"):
            evaluate_fairness(
                groups=["a", "b"], predictions=[True, False], labels=[True]
            )
