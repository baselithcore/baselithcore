"""Readiness: how a scope's evidence stands, per control and per criterion.

Pure functions over controls and evidence — no I/O, no clock of their own — so
every rule below is testable at a fixed instant.

**What "covered" means.** A control is *covered* when evidence of it exists in
the examined window, the latest is recent enough for how often the control is
performed, and the latest does not report a deviation. It is the answer to the
question an auditor's sample begins with: *can you show me this control
operating?* It is not a statement that the control is *effective*; that is the
auditor's opinion.

Statuses, worst first:

* ``exception`` — the latest evidence reports a deviation, not yet followed by
  evidence that it operated again;
* ``untested`` — no evidence in the examined window;
* ``intermittent`` — (Type II only) evidence exists but covers too few of the
  expected intervals of the observation period;
* ``stale`` — the latest evidence is older than the control's frequency allows;
* ``covered``.

A **criterion** is judged by the worst status among the active controls mapped
to it, and is ``uncovered`` when none is mapped: one control with a gap is a
gap, even if a second one is fine. The conservative reading is deliberate; a
readiness view that rounds up is the wrong place to be generous.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from baselith.core.compliance.soc2.catalogue import Criterion, criteria_for
from baselith.core.compliance.soc2.types import (
    FREQUENCY_DAYS,
    AttestationType,
    EvidenceOutcome,
    Soc2Control,
    Soc2Evidence,
    Soc2Scope,
)

#: Worst first. ``uncovered`` only exists at the criterion level.
STATUS_RANK: dict[str, int] = {
    "uncovered": 6,
    "exception": 5,
    "untested": 4,
    "intermittent": 3,
    "stale": 2,
    "covered": 1,
}

#: How much later than its nominal interval the latest evidence may be before
#: the control reads as stale: a weekly control observed 10 days ago is fine.
STALE_FACTOR = 1.5

#: Type II: the share of expected intervals that must hold evidence.
MIN_COVERAGE = 0.75


@dataclass
class ControlReadiness:
    control_id: str
    title: str
    status: str
    evidence_count: int = 0
    latest_evidence_at: datetime | None = None
    #: Type II only: the share of expected intervals holding evidence.
    coverage: float | None = None
    exceptions_in_window: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "title": self.title,
            "status": self.status,
            "evidence_count": self.evidence_count,
            "latest_evidence_at": (
                self.latest_evidence_at.isoformat() if self.latest_evidence_at else None
            ),
            "coverage": self.coverage,
            "exceptions_in_window": self.exceptions_in_window,
        }


@dataclass
class CriterionReadiness:
    criterion: Criterion
    status: str
    control_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.criterion.to_dict(),
            "status": self.status,
            "control_ids": list(self.control_ids),
        }


def judged_at(scope: Soc2Scope, now: datetime) -> datetime:
    """The instant the scope is judged at: the end of its as-of day, or now.

    An open Type II period (no end date) is judged up to now.
    """
    if scope.period_end is None and scope.attestation_type is AttestationType.TYPE_II:
        return now
    end = datetime.combine(scope.as_of, time(23, 59, 59), tzinfo=UTC)
    return min(now, end)


def _window(scope: Soc2Scope, now: datetime) -> tuple[datetime | None, datetime]:
    upper = judged_at(scope, now)
    if scope.attestation_type is AttestationType.TYPE_I:
        # A design is examined as of a date: everything before it counts.
        return None, upper
    return datetime.combine(scope.period_start, time.min, tzinfo=UTC), upper


def _coverage(
    evidence: list[Soc2Evidence],
    start: datetime,
    end: datetime,
    interval_days: int,
) -> float:
    """The share of ``interval_days`` buckets of ``[start, end]`` holding evidence."""
    span = max((end - start).total_seconds(), 0.0)
    step = interval_days * 86400
    buckets = max(1, math.ceil(span / step))
    hit = {
        min(int((e.collected_at - start).total_seconds() // step), buckets - 1)
        for e in evidence
    }
    return round(len(hit) / buckets, 3)


def assess_control(
    control: Soc2Control,
    evidence: list[Soc2Evidence],
    scope: Soc2Scope,
    now: datetime,
) -> ControlReadiness:
    """Where one control stands against ``scope`` at ``now``."""
    start, end = _window(scope, now)
    inside = sorted(
        (
            e
            for e in evidence
            if e.control_id == control.id
            and e.collected_at <= end
            and (start is None or e.collected_at >= start)
        ),
        key=lambda e: e.collected_at,
    )
    result = ControlReadiness(control.id, control.title, "untested", len(inside))
    if not inside:
        return result
    latest = inside[-1]
    result.latest_evidence_at = latest.collected_at
    result.exceptions_in_window = sum(
        1 for e in inside if e.outcome is EvidenceOutcome.EXCEPTION
    )
    interval = FREQUENCY_DAYS[control.frequency]
    status = "covered"
    if interval is not None and end - latest.collected_at > timedelta(
        days=interval * STALE_FACTOR
    ):
        status = "stale"
    if interval is not None and scope.attestation_type is AttestationType.TYPE_II:
        assert start is not None
        result.coverage = _coverage(inside, start, end, interval)
        if result.coverage < MIN_COVERAGE and status == "covered":
            status = "intermittent"
    if latest.outcome is EvidenceOutcome.EXCEPTION:
        status = "exception"
    result.status = status
    return result


def assess_scope(
    scope: Soc2Scope,
    controls: list[Soc2Control],
    evidence: list[Soc2Evidence],
    now: datetime,
) -> tuple[list[CriterionReadiness], list[ControlReadiness]]:
    """Every in-scope criterion and every active control, assessed."""
    active = [c for c in controls if c.active]
    by_control = {c.id: assess_control(c, evidence, scope, now) for c in active}
    criteria: list[CriterionReadiness] = []
    for criterion in criteria_for(scope.categories):
        mapped = [c for c in active if criterion.id in c.criteria]
        if not mapped:
            criteria.append(CriterionReadiness(criterion, "uncovered"))
            continue
        worst = max((by_control[c.id].status for c in mapped), key=STATUS_RANK.get)  # type: ignore[arg-type]
        criteria.append(CriterionReadiness(criterion, worst, [c.id for c in mapped]))
    return criteria, list(by_control.values())


def summarise(criteria: list[CriterionReadiness]) -> dict[str, Any]:
    """Counts per status and per category, and whether anything is open."""
    counts = dict.fromkeys(STATUS_RANK, 0)
    by_category: dict[str, dict[str, int]] = {}
    for row in criteria:
        counts[row.status] += 1
        cat = by_category.setdefault(
            row.criterion.category.value, dict.fromkeys(STATUS_RANK, 0)
        )
        cat[row.status] += 1
    gaps = sum(n for status, n in counts.items() if status != "covered")
    return {
        "criteria": len(criteria),
        "counts": counts,
        "by_category": by_category,
        "gaps": gaps,
        "ready": gaps == 0 and bool(criteria),
    }


__all__ = [
    "MIN_COVERAGE",
    "STALE_FACTOR",
    "STATUS_RANK",
    "ControlReadiness",
    "CriterionReadiness",
    "assess_control",
    "assess_scope",
    "judged_at",
    "summarise",
]
