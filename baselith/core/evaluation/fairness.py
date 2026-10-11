"""Group fairness metrics for bias examination.

Two obligations make these numbers mandatory rather than nice to have:

* **AI Act Art. 10(2)(f)/(g)** — training, validation and testing data sets for
  high-risk systems are subject to an examination in view of **possible biases**
  likely to affect health and safety, have a negative impact on fundamental
  rights, or lead to discrimination, together with appropriate measures to
  detect, prevent and mitigate them.
* **AI Act Art. 15** — the declared level of accuracy must hold, and it must
  hold across the groups the system acts on, not only in aggregate.

The module computes the standard group-fairness quantities over labelled
outcomes and returns them with the per-group detail, because an aggregate
fairness score with no breakdown cannot support the "appropriate measures to
detect" half of Art. 10.

**What these numbers are not.** Fairness metrics are mutually incompatible in
general — demographic parity and equalized odds cannot both hold when base rates
differ across groups — so a passing threshold on one is not evidence of an
unbiased system. Choosing which criterion matters for a given system is a
substantive decision that belongs in the Art. 9 risk management file, and the
choice should be recorded there. This module makes the measurement reproducible;
it does not make the choice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

#: Common rule-of-thumb floor for the disparate impact ratio (the "four-fifths
#: rule" of US employment-selection practice). It has **no standing in EU law**
#: and is offered only as a default starting point for a threshold the operator
#: must justify for their own context.
FOUR_FIFTHS = 0.8


@dataclass
class GroupOutcome:
    """Confusion-matrix counts and rates for one protected group."""

    group: str
    total: int = 0
    positive_predictions: int = 0
    true_positives: int = 0
    false_positives: int = 0
    true_negatives: int = 0
    false_negatives: int = 0

    @property
    def selection_rate(self) -> float:
        """Share of the group receiving the positive outcome."""
        return self.positive_predictions / self.total if self.total else 0.0

    @property
    def true_positive_rate(self) -> float:
        """Recall within the group (sensitivity)."""
        actual_positives = self.true_positives + self.false_negatives
        return self.true_positives / actual_positives if actual_positives else 0.0

    @property
    def false_positive_rate(self) -> float:
        """Share of the group's actual negatives predicted positive."""
        actual_negatives = self.false_positives + self.true_negatives
        return self.false_positives / actual_negatives if actual_negatives else 0.0

    @property
    def accuracy(self) -> float:
        """Share of correct predictions within the group."""
        correct = self.true_positives + self.true_negatives
        return correct / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "total": self.total,
            "positive_predictions": self.positive_predictions,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "false_negatives": self.false_negatives,
            "selection_rate": self.selection_rate,
            "true_positive_rate": self.true_positive_rate,
            "false_positive_rate": self.false_positive_rate,
            "accuracy": self.accuracy,
        }


@dataclass
class FairnessReport:
    """Group fairness metrics over a labelled evaluation set."""

    groups: list[GroupOutcome] = field(default_factory=list)
    #: Threshold the operator chose for the disparate impact ratio.
    disparate_impact_threshold: float = FOUR_FIFTHS
    #: Maximum tolerated difference for the parity/odds gaps.
    max_difference: float = 0.1

    @property
    def demographic_parity_difference(self) -> float:
        """Largest gap in selection rate between any two groups.

        Zero means every group is selected at the same rate. This ignores the
        ground truth entirely — which is the point, and also the limitation.
        """
        rates = [g.selection_rate for g in self.groups]
        return max(rates) - min(rates) if rates else 0.0

    @property
    def disparate_impact_ratio(self) -> float:
        """Ratio of the lowest to the highest selection rate (1.0 is parity).

        Returns 0.0 when the highest selection rate is zero — no group was
        selected at all, so no ratio is defined and the permissive answer would
        be misleading.
        """
        rates = [g.selection_rate for g in self.groups]
        if not rates or max(rates) == 0:
            return 0.0
        return min(rates) / max(rates)

    @property
    def equal_opportunity_difference(self) -> float:
        """Largest gap in true-positive rate between any two groups."""
        rates = [g.true_positive_rate for g in self.groups]
        return max(rates) - min(rates) if rates else 0.0

    @property
    def equalized_odds_difference(self) -> float:
        """The larger of the TPR gap and the FPR gap across groups.

        Equalized odds requires *both* rates to match, so the binding constraint
        is the worse of the two.
        """
        fpr = [g.false_positive_rate for g in self.groups]
        fpr_gap = max(fpr) - min(fpr) if fpr else 0.0
        return max(self.equal_opportunity_difference, fpr_gap)

    @property
    def accuracy_difference(self) -> float:
        """Largest gap in accuracy between any two groups (Art. 15)."""
        values = [g.accuracy for g in self.groups]
        return max(values) - min(values) if values else 0.0

    def violations(self) -> list[str]:
        """Which configured thresholds this report breaches.

        An empty list means "no configured threshold was breached" — not "the
        system is fair". See the module docstring.
        """
        breaches: list[str] = []
        if len(self.groups) < 2:
            return breaches
        if self.disparate_impact_ratio < self.disparate_impact_threshold:
            breaches.append(
                f"disparate impact ratio {self.disparate_impact_ratio:.3f} "
                f"below threshold {self.disparate_impact_threshold:.3f}"
            )
        if self.demographic_parity_difference > self.max_difference:
            breaches.append(
                f"demographic parity difference "
                f"{self.demographic_parity_difference:.3f} above "
                f"{self.max_difference:.3f}"
            )
        if self.equalized_odds_difference > self.max_difference:
            breaches.append(
                f"equalized odds difference {self.equalized_odds_difference:.3f} "
                f"above {self.max_difference:.3f}"
            )
        if self.accuracy_difference > self.max_difference:
            breaches.append(
                f"accuracy difference {self.accuracy_difference:.3f} above "
                f"{self.max_difference:.3f}"
            )
        return breaches

    @property
    def passed(self) -> bool:
        """Whether no configured threshold was breached."""
        return not self.violations()

    def to_dict(self) -> dict[str, Any]:
        return {
            "groups": [g.to_dict() for g in self.groups],
            "demographic_parity_difference": self.demographic_parity_difference,
            "disparate_impact_ratio": self.disparate_impact_ratio,
            "equal_opportunity_difference": self.equal_opportunity_difference,
            "equalized_odds_difference": self.equalized_odds_difference,
            "accuracy_difference": self.accuracy_difference,
            "disparate_impact_threshold": self.disparate_impact_threshold,
            "max_difference": self.max_difference,
            "violations": self.violations(),
            "passed": self.passed,
        }


def evaluate_fairness(
    groups: Sequence[str],
    predictions: Sequence[bool],
    labels: Sequence[bool] | None = None,
    *,
    disparate_impact_threshold: float = FOUR_FIFTHS,
    max_difference: float = 0.1,
) -> FairnessReport:
    """Compute group fairness metrics over aligned outcome sequences.

    Args:
        groups: Protected-attribute value per sample (e.g. ``"a"``/``"b"``).
        predictions: The model's positive/negative decision per sample.
        labels: Ground truth per sample. Optional — without it only the
            label-free metrics (selection rate, demographic parity, disparate
            impact) are meaningful; the rate-based ones read as zero.
        disparate_impact_threshold: Floor for the selection-rate ratio.
        max_difference: Ceiling for the parity, odds and accuracy gaps.

    Raises:
        ValueError: If the sequences have different lengths — a silent zip would
            drop samples and quietly bias the very measurement being taken.
    """
    if len(groups) != len(predictions):
        raise ValueError(
            f"groups and predictions must align: {len(groups)} != {len(predictions)}"
        )
    if labels is not None and len(labels) != len(groups):
        raise ValueError(
            f"labels must align with groups: {len(labels)} != {len(groups)}"
        )

    outcomes: dict[str, GroupOutcome] = {}
    for index, group in enumerate(groups):
        outcome = outcomes.setdefault(group, GroupOutcome(group=group))
        predicted = bool(predictions[index])
        outcome.total += 1
        if predicted:
            outcome.positive_predictions += 1
        if labels is None:
            continue
        actual = bool(labels[index])
        if predicted and actual:
            outcome.true_positives += 1
        elif predicted and not actual:
            outcome.false_positives += 1
        elif not predicted and actual:
            outcome.false_negatives += 1
        else:
            outcome.true_negatives += 1

    return FairnessReport(
        groups=[outcomes[g] for g in sorted(outcomes)],
        disparate_impact_threshold=disparate_impact_threshold,
        max_difference=max_difference,
    )


__all__ = [
    "FOUR_FIFTHS",
    "FairnessReport",
    "GroupOutcome",
    "evaluate_fairness",
]
