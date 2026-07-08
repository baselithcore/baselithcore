"""DORA major-incident service — Regulation (EU) 2022/2554 Art. 19 workflow.

Records major ICT-related incidents, classifies them against the DORA criteria,
advances them through the regulatory milestones (initial notification →
intermediate report → final report → closed), and surfaces overdue obligations
so the 4h/72h/one-month clock is never silently missed. Every transition emits
an ``AUDIT | DORA-INCIDENT | …`` log line for traceability.

The store is a Protocol with an in-memory reference implementation; production
deployments register a durable store. Outbound regulatory filing (to the
competent authority) remains the operator's action — this subsystem produces and
tracks the structured record that backs it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.config.incidents import IncidentReportingConfig, get_incident_config
from core.incidents.dora import (
    DoraClassification,
    DoraImpactAssessment,
    DoraIncident,
    DoraIncidentStatus,
)
from core.incidents.types import IncidentSeverity, ReportingMilestone, _utcnow
from core.observability.logging import get_logger

logger = get_logger(__name__)


class DoraIncidentStore(Protocol):
    """Persistence boundary for DORA major incidents."""

    async def save(self, incident: DoraIncident) -> None:
        """Insert or update an incident."""
        ...

    async def get(self, incident_id: str) -> DoraIncident | None:
        """Fetch an incident by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> list[DoraIncident]:
        """Return every stored incident."""
        ...


class InMemoryDoraIncidentStore:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._incidents: dict[str, DoraIncident] = {}

    async def save(self, incident: DoraIncident) -> None:
        self._incidents[incident.id] = incident

    async def get(self, incident_id: str) -> DoraIncident | None:
        return self._incidents.get(incident_id)

    async def list_all(self) -> list[DoraIncident]:
        return list(self._incidents.values())


class DoraIncidentNotFoundError(Exception):
    """Raised when an operation references an unknown incident id."""


class DoraIncidentService:
    """Open, classify, and advance major incidents against the DORA clock."""

    def __init__(
        self,
        store: DoraIncidentStore | None = None,
        config: IncidentReportingConfig | None = None,
    ) -> None:
        self._store = store or InMemoryDoraIncidentStore()
        self._config = config or get_incident_config()

    @property
    def store(self) -> DoraIncidentStore:
        return self._store

    async def open_incident(
        self,
        title: str,
        severity: IncidentSeverity = IncidentSeverity.HIGH,
        *,
        description: str = "",
        affected_systems: list[str] | None = None,
        affected_clients: int = 0,
        detected_at: datetime | None = None,
        details: dict[str, object] | None = None,
    ) -> DoraIncident:
        """Record a newly detected incident (status ``DETECTED``, unclassified).

        Args:
            title: Short human-readable incident title.
            severity: Assessed severity band.
            description: Free-text description of scope/impact.
            affected_systems: Identifiers of impacted systems/services.
            affected_clients: Count of affected clients/financial counterparts.
            detected_at: When the entity became aware (defaults to now); the
                24h awareness cap is anchored to this moment.
            details: Arbitrary structured metadata.
        """
        incident = DoraIncident(
            title=title,
            severity=severity,
            description=description,
            affected_systems=list(affected_systems or []),
            affected_clients=affected_clients,
            details=dict(details or {}),
        )
        if detected_at is not None:
            incident.detected_at = detected_at
        await self._store.save(incident)
        logger.info(
            "AUDIT | DORA-INCIDENT | opened | id=%s severity=%s",
            incident.id,
            incident.severity.value,
        )
        return incident

    async def classify(
        self,
        incident_id: str,
        assessment: DoraImpactAssessment,
        *,
        classified_at: datetime | None = None,
        major_override: bool | None = None,
    ) -> DoraIncident:
        """Attach a classification; if major, start the reporting clock.

        ``classified_at`` anchors the 4h initial-notification deadline and
        defaults to now. The status advances to ``CLASSIFIED`` only when the
        incident qualifies as major.
        """
        incident = await self._require(incident_id)
        classification = DoraClassification(
            assessment=assessment,
            classified_at=classified_at or _utcnow(),
            major_override=major_override,
        )
        incident.classification = classification
        incident.updated_at = classification.classified_at
        if classification.is_major and _rank(DoraIncidentStatus.CLASSIFIED) > _rank(
            incident.status
        ):
            incident.status = DoraIncidentStatus.CLASSIFIED
        await self._store.save(incident)
        logger.info(
            "AUDIT | DORA-INCIDENT | classified | id=%s major=%s at=%s",
            incident.id,
            classification.is_major,
            classification.classified_at.isoformat(),
        )
        return incident

    async def _advance(
        self,
        incident_id: str,
        *,
        field_name: str,
        status: DoraIncidentStatus,
        submitted_at: datetime | None,
        milestone: str,
    ) -> DoraIncident:
        """Stamp a milestone submission and advance status (no regression)."""
        incident = await self._require(incident_id)
        stamp = submitted_at or _utcnow()
        setattr(incident, field_name, stamp)
        if _rank(status) > _rank(incident.status):
            incident.status = status
        incident.updated_at = stamp
        await self._store.save(incident)
        logger.info(
            "AUDIT | DORA-INCIDENT | %s | id=%s at=%s",
            milestone,
            incident.id,
            stamp.isoformat(),
        )
        return incident

    async def record_initial_notification(
        self, incident_id: str, *, submitted_at: datetime | None = None
    ) -> DoraIncident:
        """Mark the 4h initial notification as submitted."""
        return await self._advance(
            incident_id,
            field_name="initial_notification_at",
            status=DoraIncidentStatus.INITIAL_SUBMITTED,
            submitted_at=submitted_at,
            milestone="initial_notification",
        )

    async def record_intermediate_report(
        self, incident_id: str, *, submitted_at: datetime | None = None
    ) -> DoraIncident:
        """Mark the 72h intermediate report as submitted."""
        return await self._advance(
            incident_id,
            field_name="intermediate_report_at",
            status=DoraIncidentStatus.INTERMEDIATE_SUBMITTED,
            submitted_at=submitted_at,
            milestone="intermediate_report",
        )

    async def record_final_report(
        self, incident_id: str, *, submitted_at: datetime | None = None
    ) -> DoraIncident:
        """Mark the one-month final report as submitted."""
        return await self._advance(
            incident_id,
            field_name="final_report_at",
            status=DoraIncidentStatus.FINAL_SUBMITTED,
            submitted_at=submitted_at,
            milestone="final_report",
        )

    async def close_incident(
        self, incident_id: str, *, closed_at: datetime | None = None
    ) -> DoraIncident:
        """Close an incident (reporting obligations fulfilled or not applicable)."""
        return await self._advance(
            incident_id,
            field_name="closed_at",
            status=DoraIncidentStatus.CLOSED,
            submitted_at=closed_at,
            milestone="closed",
        )

    async def get(self, incident_id: str) -> DoraIncident | None:
        """Fetch an incident by id."""
        return await self._store.get(incident_id)

    async def list_incidents(
        self, *, status: DoraIncidentStatus | None = None
    ) -> list[DoraIncident]:
        """List incidents, optionally filtered by status."""
        incidents = await self._store.list_all()
        if status is not None:
            incidents = [i for i in incidents if i.status == status]
        return incidents

    async def list_open(self) -> list[DoraIncident]:
        """List incidents that are not yet closed."""
        return [
            i
            for i in await self._store.list_all()
            if i.status != DoraIncidentStatus.CLOSED
        ]

    def milestones(self, incident: DoraIncident) -> list[ReportingMilestone]:
        """Compute the DORA reporting milestones using configured deadlines."""
        return incident.milestones(
            initial_hours=self._config.dora_initial_notification_hours,
            awareness_cap_hours=self._config.dora_awareness_cap_hours,
            intermediate_hours=self._config.dora_intermediate_report_hours,
            final_days=self._config.dora_final_report_days,
        )

    async def overdue_milestones(
        self, now: datetime | None = None
    ) -> list[tuple[DoraIncident, ReportingMilestone]]:
        """Return ``(incident, milestone)`` pairs with a missed, unmet deadline.

        Closed incidents are skipped. Use to drive escalation/alerting so a
        regulatory deadline cannot pass unnoticed.
        """
        overdue: list[tuple[DoraIncident, ReportingMilestone]] = []
        for incident in await self._store.list_all():
            if incident.status == DoraIncidentStatus.CLOSED:
                continue
            for milestone in self.milestones(incident):
                if milestone.is_overdue(now):
                    overdue.append((incident, milestone))
        return overdue

    async def _require(self, incident_id: str) -> DoraIncident:
        incident = await self._store.get(incident_id)
        if incident is None:
            raise DoraIncidentNotFoundError(incident_id)
        return incident


def _rank(status: DoraIncidentStatus) -> int:
    """Order statuses so transitions only move forward."""
    order = {
        DoraIncidentStatus.DETECTED: 0,
        DoraIncidentStatus.CLASSIFIED: 1,
        DoraIncidentStatus.INITIAL_SUBMITTED: 2,
        DoraIncidentStatus.INTERMEDIATE_SUBMITTED: 3,
        DoraIncidentStatus.FINAL_SUBMITTED: 4,
        DoraIncidentStatus.CLOSED: 5,
    }
    return order[status]


_service: DoraIncidentService | None = None


def _build_dora_incident_service() -> DoraIncidentService:
    """Build the service, selecting a durable store iff ``DORA_DB_PATH`` is set.

    Unset (the default) constructs the service exactly as before — over the
    non-durable in-memory store — so behaviour is byte-for-byte unchanged.
    """
    path = get_incident_config().dora_db_path
    if path:
        from core.incidents.persistence import SQLiteDoraIncidentStore

        return DoraIncidentService(store=SQLiteDoraIncidentStore(path))
    return DoraIncidentService()


def get_dora_incident_service() -> DoraIncidentService:
    """Get or create the global DORA incident service.

    Uses a durable SQLite store when ``DORA_DB_PATH`` is configured, else the
    in-memory reference store (the default).
    """
    global _service
    if _service is None:
        _service = _build_dora_incident_service()
    return _service


__all__ = [
    "DoraIncidentNotFoundError",
    "DoraIncidentService",
    "DoraIncidentStore",
    "InMemoryDoraIncidentStore",
    "get_dora_incident_service",
]
