"""Security-incident service — NIS2 Art. 23 reporting workflow.

Records significant security incidents, advances them through the regulatory
milestones (early warning → notification → final report → closed), and surfaces
overdue obligations so the 24h/72h/one-month clock is never silently missed.
Every transition emits an ``AUDIT | INCIDENT | …`` log line for traceability.

The store is a Protocol with an in-memory reference implementation; production
deployments register a durable store (e.g. Postgres) the same way other
subsystems do. Outbound regulatory notification (to a national CSIRT) is the
operator's action — this subsystem produces and tracks the structured record
that backs it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional, Protocol, Tuple

from core.config.incidents import IncidentReportingConfig, get_incident_config
from core.incidents.types import (
    IncidentSeverity,
    IncidentStatus,
    ReportingMilestone,
    SecurityIncident,
)
from core.observability.logging import get_logger

logger = get_logger(__name__)


class IncidentStore(Protocol):
    """Persistence boundary for security incidents."""

    async def save(self, incident: SecurityIncident) -> None:
        """Insert or update an incident."""
        ...

    async def get(self, incident_id: str) -> Optional[SecurityIncident]:
        """Fetch an incident by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> List[SecurityIncident]:
        """Return every stored incident."""
        ...


class InMemoryIncidentStore:
    """Reference in-memory incident store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._incidents: Dict[str, SecurityIncident] = {}

    async def save(self, incident: SecurityIncident) -> None:
        self._incidents[incident.id] = incident

    async def get(self, incident_id: str) -> Optional[SecurityIncident]:
        return self._incidents.get(incident_id)

    async def list_all(self) -> List[SecurityIncident]:
        return list(self._incidents.values())


class IncidentNotFoundError(Exception):
    """Raised when an operation references an unknown incident id."""


class IncidentService:
    """Open and advance security incidents against the NIS2 reporting clock."""

    def __init__(
        self,
        store: Optional[IncidentStore] = None,
        config: Optional[IncidentReportingConfig] = None,
    ) -> None:
        self._store = store or InMemoryIncidentStore()
        self._config = config or get_incident_config()

    @property
    def store(self) -> IncidentStore:
        return self._store

    async def open_incident(
        self,
        title: str,
        severity: IncidentSeverity,
        *,
        significant: bool = True,
        description: str = "",
        affected_systems: Optional[List[str]] = None,
        affected_subjects: int = 0,
        detected_at: Optional[datetime] = None,
        details: Optional[Dict[str, object]] = None,
    ) -> SecurityIncident:
        """Record a newly detected incident (status ``DETECTED``).

        Args:
            title: Short human-readable incident title.
            severity: Assessed severity band.
            significant: Whether the incident triggers NIS2 reporting deadlines.
            description: Free-text description of scope/impact.
            affected_systems: Identifiers of impacted systems/services.
            affected_subjects: Count of affected data subjects/users.
            detected_at: When the entity became aware (defaults to now); the
                reporting clock is anchored to this moment.
            details: Arbitrary structured metadata.
        """
        incident = SecurityIncident(
            title=title,
            severity=severity,
            significant=significant,
            description=description,
            affected_systems=list(affected_systems or []),
            affected_subjects=affected_subjects,
            details=dict(details or {}),
        )
        if detected_at is not None:
            incident.detected_at = detected_at
        await self._store.save(incident)
        logger.info(
            "AUDIT | INCIDENT | opened | id=%s severity=%s significant=%s",
            incident.id,
            incident.severity.value,
            incident.significant,
        )
        return incident

    async def _advance(
        self,
        incident_id: str,
        *,
        field_name: str,
        status: IncidentStatus,
        submitted_at: Optional[datetime],
        milestone: str,
    ) -> SecurityIncident:
        """Stamp a milestone submission and advance status (no regression)."""
        incident = await self._store.get(incident_id)
        if incident is None:
            raise IncidentNotFoundError(incident_id)
        stamp = submitted_at or datetime_now()
        setattr(incident, field_name, stamp)
        # Only move the status forward, never back.
        if _status_rank(status) > _status_rank(incident.status):
            incident.status = status
        incident.updated_at = stamp
        await self._store.save(incident)
        logger.info(
            "AUDIT | INCIDENT | %s | id=%s at=%s",
            milestone,
            incident.id,
            stamp.isoformat(),
        )
        return incident

    async def record_early_warning(
        self, incident_id: str, *, submitted_at: Optional[datetime] = None
    ) -> SecurityIncident:
        """Mark the 24h early warning as submitted."""
        return await self._advance(
            incident_id,
            field_name="early_warning_at",
            status=IncidentStatus.EARLY_WARNING_SUBMITTED,
            submitted_at=submitted_at,
            milestone="early_warning",
        )

    async def record_notification(
        self, incident_id: str, *, submitted_at: Optional[datetime] = None
    ) -> SecurityIncident:
        """Mark the 72h incident notification as submitted."""
        return await self._advance(
            incident_id,
            field_name="notification_at",
            status=IncidentStatus.NOTIFICATION_SUBMITTED,
            submitted_at=submitted_at,
            milestone="notification",
        )

    async def record_final_report(
        self, incident_id: str, *, submitted_at: Optional[datetime] = None
    ) -> SecurityIncident:
        """Mark the one-month final report as submitted."""
        return await self._advance(
            incident_id,
            field_name="final_report_at",
            status=IncidentStatus.FINAL_SUBMITTED,
            submitted_at=submitted_at,
            milestone="final_report",
        )

    async def close_incident(
        self, incident_id: str, *, closed_at: Optional[datetime] = None
    ) -> SecurityIncident:
        """Close an incident (reporting obligations fulfilled or not applicable)."""
        return await self._advance(
            incident_id,
            field_name="closed_at",
            status=IncidentStatus.CLOSED,
            submitted_at=closed_at,
            milestone="closed",
        )

    async def get(self, incident_id: str) -> Optional[SecurityIncident]:
        """Fetch an incident by id."""
        return await self._store.get(incident_id)

    async def list_incidents(
        self, *, status: Optional[IncidentStatus] = None
    ) -> List[SecurityIncident]:
        """List incidents, optionally filtered by status."""
        incidents = await self._store.list_all()
        if status is not None:
            incidents = [i for i in incidents if i.status == status]
        return incidents

    async def list_open(self) -> List[SecurityIncident]:
        """List incidents that are not yet closed."""
        return [
            i for i in await self._store.list_all() if i.status != IncidentStatus.CLOSED
        ]

    def milestones(self, incident: SecurityIncident) -> List[ReportingMilestone]:
        """Compute the reporting milestones for ``incident`` using configured deadlines."""
        return incident.milestones(
            early_warning_hours=self._config.early_warning_hours,
            notification_hours=self._config.notification_hours,
            final_report_days=self._config.final_report_days,
        )

    async def overdue_milestones(
        self, now: Optional[datetime] = None
    ) -> List[Tuple[SecurityIncident, ReportingMilestone]]:
        """Return ``(incident, milestone)`` pairs with a missed, unmet deadline.

        Closed incidents are skipped. Use to drive escalation/alerting so a
        regulatory deadline cannot pass unnoticed.
        """
        overdue: List[Tuple[SecurityIncident, ReportingMilestone]] = []
        for incident in await self._store.list_all():
            if incident.status == IncidentStatus.CLOSED:
                continue
            for milestone in self.milestones(incident):
                if milestone.is_overdue(now):
                    overdue.append((incident, milestone))
        return overdue


def _status_rank(status: IncidentStatus) -> int:
    """Order statuses so transitions only move forward."""
    order = {
        IncidentStatus.DETECTED: 0,
        IncidentStatus.EARLY_WARNING_SUBMITTED: 1,
        IncidentStatus.NOTIFICATION_SUBMITTED: 2,
        IncidentStatus.FINAL_SUBMITTED: 3,
        IncidentStatus.CLOSED: 4,
    }
    return order[status]


def datetime_now() -> datetime:
    """Timezone-aware UTC now (module-level for test monkeypatching)."""
    from core.incidents.types import _utcnow

    return _utcnow()


_service: Optional[IncidentService] = None


def get_incident_service() -> IncidentService:
    """Get or create the global incident service over an in-memory store."""
    global _service
    if _service is None:
        _service = IncidentService()
    return _service


__all__ = [
    "IncidentStore",
    "InMemoryIncidentStore",
    "IncidentService",
    "IncidentNotFoundError",
    "get_incident_service",
]
