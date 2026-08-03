"""Art. 72 post-market monitoring.

Conformity assessment is a snapshot; Art. 72 is the obligation that the snapshot
keeps being true. Providers of high-risk AI systems must establish a post-market
monitoring **system proportionate to the risks**, actively collect and analyse
data on the system's performance throughout its lifetime, and document it in a
monitoring **plan** that forms part of the Annex IV technical documentation.

The evaluation framework already scores a model *before* release
(:mod:`core.evaluation`). This module is what makes the production side of the
loop explicit: which metrics are watched, at what threshold, how often the plan
is reviewed, and what happens when a threshold is breached — a breach is the
usual trigger for the Art. 73 serious-incident question, so it must be a visible
event rather than a dashboard nobody opened.

The module records and evaluates. Emitting the alert, opening the incident, and
taking the corrective action stay with the operator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from uuid import uuid4

from core.compliance.types import _iso, _parse_dt, _utcnow


class ThresholdDirection(str, Enum):
    """Which side of the threshold counts as a breach."""

    #: Breach when the observed value falls below the threshold (accuracy, …).
    LOWER_BOUND = "lower_bound"
    #: Breach when the observed value rises above it (error rate, latency, …).
    UPPER_BOUND = "upper_bound"


@dataclass
class MonitoringMetric:
    """One quantity watched in production, with the threshold that alerts."""

    name: str
    description: str = ""
    unit: str = ""
    threshold: float | None = None
    direction: ThresholdDirection = ThresholdDirection.LOWER_BOUND
    #: Free-form pointer to where the number comes from (dashboard, job, table).
    source: str = ""

    def is_breach(self, value: float) -> bool:
        """Whether ``value`` breaches this metric's threshold.

        A metric with no threshold is *observed but not alerting* — collecting
        it still satisfies the Art. 72 duty to gather data; it simply cannot
        trigger anything on its own.
        """
        if self.threshold is None:
            return False
        if self.direction is ThresholdDirection.LOWER_BOUND:
            return value < self.threshold
        return value > self.threshold

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "threshold": self.threshold,
            "direction": self.direction.value,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MonitoringMetric:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            unit=data.get("unit", ""),
            threshold=data.get("threshold"),
            direction=ThresholdDirection(
                data.get("direction", ThresholdDirection.LOWER_BOUND.value)
            ),
            source=data.get("source", ""),
        )


@dataclass
class PostMarketObservation:
    """A single measurement taken in production against a plan metric."""

    metric: str
    value: float
    observed_at: datetime = field(default_factory=_utcnow)
    is_breach: bool = False
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "value": self.value,
            "observed_at": self.observed_at.isoformat(),
            "is_breach": self.is_breach,
            "context": self.context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostMarketObservation:
        return cls(
            metric=data["metric"],
            value=data["value"],
            observed_at=datetime.fromisoformat(data["observed_at"]),
            is_breach=data.get("is_breach", False),
            context=dict(data.get("context", {})),
        )


@dataclass
class PostMarketMonitoringPlan:
    """The Art. 72 monitoring plan for one high-risk AI system."""

    system_id: str
    objectives: str = ""
    metrics: list[MonitoringMetric] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    #: How often the plan itself is reviewed — Art. 72(1) requires the system be
    #: kept *active*, which a plan reviewed once at launch is not.
    review_cadence_days: int = 90
    corrective_action_process: str = ""
    #: Who owns the monitoring, mirroring the Art. 14 oversight assignment.
    responsible_contacts: list[str] = field(default_factory=list)
    observations: list[PostMarketObservation] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    last_reviewed_at: datetime | None = None

    def metric(self, name: str) -> MonitoringMetric | None:
        """Look up a metric by name."""
        return next((m for m in self.metrics if m.name == name), None)

    def observe(
        self,
        metric_name: str,
        value: float,
        *,
        at: datetime | None = None,
        context: dict[str, Any] | None = None,
    ) -> PostMarketObservation:
        """Record a production measurement and evaluate it against the threshold.

        Raises :class:`KeyError` for a metric the plan does not declare — an
        observation nobody planned for is a plan gap, not a silent append.
        """
        metric = self.metric(metric_name)
        if metric is None:
            raise KeyError(f"Metric not declared in the monitoring plan: {metric_name}")
        observation = PostMarketObservation(
            metric=metric_name,
            value=value,
            observed_at=at or _utcnow(),
            is_breach=metric.is_breach(value),
            context=dict(context or {}),
        )
        self.observations.append(observation)
        self.updated_at = observation.observed_at
        return observation

    def breaches(self, *, since: datetime | None = None) -> list[PostMarketObservation]:
        """Observations that breached their threshold, optionally since a moment."""
        return [
            o
            for o in self.observations
            if o.is_breach and (since is None or o.observed_at >= since)
        ]

    def review_due_at(self) -> datetime | None:
        """When the next plan review falls due, or ``None`` if never reviewed."""
        if self.last_reviewed_at is None:
            return None
        return self.last_reviewed_at + timedelta(days=self.review_cadence_days)

    def is_review_overdue(self, now: datetime | None = None) -> bool:
        """Whether the plan is past its review cadence.

        A plan that has *never* been reviewed counts as overdue once its cadence
        has elapsed since creation — "not started" is not "not due".
        """
        moment = now or _utcnow()
        due = self.review_due_at()
        if due is None:
            return moment > self.created_at + timedelta(days=self.review_cadence_days)
        return moment > due

    def missing_elements(self) -> list[str]:
        """Return the Art. 72 plan elements that are still empty."""
        missing: list[str] = []
        if not self.objectives:
            missing.append("Art. 72(1) — monitoring objectives")
        if not self.metrics:
            missing.append("Art. 72(1) — metrics collected in production")
        if not self.data_sources:
            missing.append("Art. 72(2) — data sources feeding the monitoring")
        if not self.corrective_action_process:
            missing.append("Art. 72(1) — corrective action process on a breach")
        if not self.responsible_contacts:
            missing.append("Art. 72(1) — accountable owner for the monitoring")
        return missing

    @property
    def is_complete(self) -> bool:
        """Whether every Art. 72 plan element carries content."""
        return not self.missing_elements()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "system_id": self.system_id,
            "objectives": self.objectives,
            "metrics": [m.to_dict() for m in self.metrics],
            "data_sources": list(self.data_sources),
            "review_cadence_days": self.review_cadence_days,
            "corrective_action_process": self.corrective_action_process,
            "responsible_contacts": list(self.responsible_contacts),
            "observations": [o.to_dict() for o in self.observations],
            "details": self.details,
            "missing_elements": self.missing_elements(),
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_reviewed_at": _iso(self.last_reviewed_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostMarketMonitoringPlan:
        """Reconstruct a plan from its :meth:`to_dict` payload (round-trip)."""
        return cls(
            system_id=data["system_id"],
            objectives=data.get("objectives", ""),
            metrics=[MonitoringMetric.from_dict(m) for m in data.get("metrics", [])],
            data_sources=list(data.get("data_sources", [])),
            review_cadence_days=data.get("review_cadence_days", 90),
            corrective_action_process=data.get("corrective_action_process", ""),
            responsible_contacts=list(data.get("responsible_contacts", [])),
            observations=[
                PostMarketObservation.from_dict(o)
                for o in data.get("observations", [])
            ],
            details=dict(data.get("details", {})),
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            last_reviewed_at=_parse_dt(data.get("last_reviewed_at")),
        )


__all__ = [
    "MonitoringMetric",
    "PostMarketMonitoringPlan",
    "PostMarketObservation",
    "ThresholdDirection",
]
