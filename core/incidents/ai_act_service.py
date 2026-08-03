"""AI Act serious-incident service — Regulation (EU) 2024/1689 Art. 73 workflow.

Records serious incidents involving a high-risk AI system, tracks the
category-dependent reporting clock (2 / 10 / 15 days — see
:mod:`core.incidents.ai_act`), advances them through the Art. 73 obligations
(report → complete report → investigation → corrective action → closed), and
surfaces overdue obligations so a statutory deadline is never silently missed.
Every transition emits an ``AUDIT | AI-ACT-INCIDENT | …`` log line plus a
structured audit record.

The store is a Protocol with an in-memory reference implementation; production
deployments register a durable one. Filing with the market surveillance
authority remains the operator's action — this subsystem produces and tracks the
structured record that backs it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.config.incidents import IncidentReportingConfig, get_incident_config
from core.incidents.ai_act import (
    AiActIncidentStatus,
    AiActSeriousIncident,
    SeriousIncidentCategory,
)
from core.incidents.types import IncidentSeverity, ReportingMilestone, _utcnow
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)


class AiActIncidentNotFoundError(LookupError):
    """Raised when an incident id does not resolve to a stored record."""

    def __init__(self, incident_id: str) -> None:
        super().__init__(f"AI Act serious incident not found: {incident_id}")
        self.incident_id = incident_id


class AiActIncidentStore(Protocol):
    """Persistence boundary for AI Act serious incidents."""

    async def save(self, incident: AiActSeriousIncident) -> None:
        """Insert or update an incident."""
        ...

    async def get(self, incident_id: str) -> AiActSeriousIncident | None:
        """Fetch an incident by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> list[AiActSeriousIncident]:
        """Return every stored incident."""
        ...


class InMemoryAiActIncidentStore:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._incidents: dict[str, AiActSeriousIncident] = {}

    async def save(self, incident: AiActSeriousIncident) -> None:
        self._incidents[incident.id] = incident

    async def get(self, incident_id: str) -> AiActSeriousIncident | None:
        return self._incidents.get(incident_id)

    async def list_all(self) -> list[AiActSeriousIncident]:
        return list(self._incidents.values())


def _rank(status: AiActIncidentStatus) -> int:
    """Order statuses so transitions only ever move forward."""
    order = {
        AiActIncidentStatus.DETECTED: 0,
        AiActIncidentStatus.CAUSAL_LINK_ESTABLISHED: 1,
        AiActIncidentStatus.REPORT_SUBMITTED: 2,
        AiActIncidentStatus.COMPLETE_REPORT_SUBMITTED: 3,
        AiActIncidentStatus.CLOSED: 4,
    }
    return order[status]


class AiActIncidentService:
    """Serious-incident lifecycle and Art. 73 deadline tracking."""

    def __init__(
        self,
        store: AiActIncidentStore | None = None,
        config: IncidentReportingConfig | None = None,
    ) -> None:
        self._store = store or InMemoryAiActIncidentStore()
        self._config = config or get_incident_config()

    @property
    def store(self) -> AiActIncidentStore:
        return self._store

    async def open_incident(
        self,
        title: str,
        ai_system_id: str,
        *,
        severity: IncidentSeverity = IncidentSeverity.HIGH,
        categories: list[SeriousIncidentCategory] | None = None,
        widespread_infringement: bool = False,
        serious: bool = True,
        description: str = "",
        affected_persons: int = 0,
        deployer: str | None = None,
        member_state: str | None = None,
        became_aware_at: datetime | None = None,
        details: dict[str, object] | None = None,
    ) -> AiActSeriousIncident:
        """Record a serious incident; the reporting clock anchors to awareness."""
        incident = AiActSeriousIncident(
            title=title,
            ai_system_id=ai_system_id,
            severity=severity,
            categories=list(categories or []),
            widespread_infringement=widespread_infringement,
            serious=serious,
            description=description,
            affected_persons=affected_persons,
            deployer=deployer,
            member_state=member_state,
            details=dict(details or {}),
        )
        if became_aware_at is not None:
            incident.became_aware_at = became_aware_at
        await self._store.save(incident)
        logger.info(
            "AUDIT | AI-ACT-INCIDENT | opened | id=%s system=%s serious=%s deadline_days=%d",
            incident.id,
            incident.ai_system_id,
            incident.serious,
            incident.deadline_days,
        )
        await get_audit_logger().log(
            AuditEventType.INCIDENT_OPEN,
            resource=incident.id,
            action="open",
            details={
                "regime": "ai_act",
                "ai_system_id": incident.ai_system_id,
                "serious": incident.serious,
                "categories": [c.value for c in incident.categories],
                "deadline_days": incident.deadline_days,
            },
        )
        return incident

    async def _advance(
        self,
        incident_id: str,
        *,
        field_name: str,
        status: AiActIncidentStatus | None,
        at: datetime | None,
        milestone: str,
    ) -> AiActSeriousIncident:
        """Stamp a milestone and advance status (never backwards)."""
        incident = await self._require(incident_id)
        stamp = at or _utcnow()
        setattr(incident, field_name, stamp)
        if status is not None and _rank(status) > _rank(incident.status):
            incident.status = status
        incident.updated_at = stamp
        await self._store.save(incident)
        logger.info(
            "AUDIT | AI-ACT-INCIDENT | %s | id=%s at=%s",
            milestone,
            incident.id,
            stamp.isoformat(),
        )
        await get_audit_logger().log(
            AuditEventType.INCIDENT_CLOSE
            if status is AiActIncidentStatus.CLOSED
            else AuditEventType.INCIDENT_MILESTONE,
            resource=incident.id,
            action=milestone,
            details={
                "regime": "ai_act",
                "milestone": milestone,
                "status": incident.status.value,
                "submitted_at": stamp.isoformat(),
            },
        )
        return incident

    async def record_causal_link(
        self, incident_id: str, *, established_at: datetime | None = None
    ) -> AiActSeriousIncident:
        """Record when the causal link to the AI system was established.

        Art. 73(2)/(4) require reporting *immediately* from this point, ahead of
        the outer deadline — the timestamp makes that duty auditable.
        """
        return await self._advance(
            incident_id,
            field_name="causal_link_at",
            status=AiActIncidentStatus.CAUSAL_LINK_ESTABLISHED,
            at=established_at,
            milestone="causal_link",
        )

    async def record_report(
        self, incident_id: str, *, submitted_at: datetime | None = None
    ) -> AiActSeriousIncident:
        """Mark the Art. 73(2)/(3)/(4) report to the authority as submitted."""
        return await self._advance(
            incident_id,
            field_name="report_at",
            status=AiActIncidentStatus.REPORT_SUBMITTED,
            at=submitted_at,
            milestone="serious_incident_report",
        )

    async def record_complete_report(
        self, incident_id: str, *, submitted_at: datetime | None = None
    ) -> AiActSeriousIncident:
        """Mark the Art. 73(5) complete report as submitted."""
        return await self._advance(
            incident_id,
            field_name="complete_report_at",
            status=AiActIncidentStatus.COMPLETE_REPORT_SUBMITTED,
            at=submitted_at,
            milestone="complete_report",
        )

    async def record_investigation(
        self, incident_id: str, *, started_at: datetime | None = None
    ) -> AiActSeriousIncident:
        """Record the start of the Art. 73(6) investigation and risk assessment.

        Art. 73(6) forbids altering the AI system in a way that would compromise
        the later evaluation of the incident's causes before informing the
        authorities — record the report first.
        """
        return await self._advance(
            incident_id,
            field_name="investigation_at",
            status=None,
            at=started_at,
            milestone="investigation",
        )

    async def record_corrective_action(
        self, incident_id: str, *, taken_at: datetime | None = None
    ) -> AiActSeriousIncident:
        """Record the Art. 73(6) corrective action."""
        return await self._advance(
            incident_id,
            field_name="corrective_action_at",
            status=None,
            at=taken_at,
            milestone="corrective_action",
        )

    async def close_incident(
        self, incident_id: str, *, closed_at: datetime | None = None
    ) -> AiActSeriousIncident:
        """Close an incident (obligations fulfilled or not applicable)."""
        return await self._advance(
            incident_id,
            field_name="closed_at",
            status=AiActIncidentStatus.CLOSED,
            at=closed_at,
            milestone="closed",
        )

    async def get(self, incident_id: str) -> AiActSeriousIncident | None:
        """Fetch an incident by id."""
        return await self._store.get(incident_id)

    async def list_incidents(
        self, *, status: AiActIncidentStatus | None = None
    ) -> list[AiActSeriousIncident]:
        """List incidents, optionally filtered by status."""
        incidents = await self._store.list_all()
        if status is not None:
            incidents = [i for i in incidents if i.status == status]
        return incidents

    async def list_open(self) -> list[AiActSeriousIncident]:
        """List incidents that are not yet closed."""
        return [
            i
            for i in await self._store.list_all()
            if i.status != AiActIncidentStatus.CLOSED
        ]

    def milestones(self, incident: AiActSeriousIncident) -> list[ReportingMilestone]:
        """Compute the Art. 73 milestones using the configured complete-report SLA."""
        return incident.milestones(
            complete_report_days=self._config.ai_act_complete_report_days
        )

    async def overdue_milestones(
        self, now: datetime | None = None
    ) -> list[tuple[AiActSeriousIncident, ReportingMilestone]]:
        """Return ``(incident, milestone)`` pairs with a missed, unmet deadline.

        Closed incidents are skipped. Use to drive escalation so a statutory
        deadline cannot pass unnoticed.
        """
        overdue: list[tuple[AiActSeriousIncident, ReportingMilestone]] = []
        for incident in await self._store.list_all():
            if incident.status == AiActIncidentStatus.CLOSED:
                continue
            for milestone in self.milestones(incident):
                if milestone.is_overdue(now):
                    overdue.append((incident, milestone))
        return overdue

    async def _require(self, incident_id: str) -> AiActSeriousIncident:
        incident = await self._store.get(incident_id)
        if incident is None:
            raise AiActIncidentNotFoundError(incident_id)
        return incident


_service: AiActIncidentService | None = None


def _build_ai_act_incident_service() -> AiActIncidentService:
    """Build the service, selecting a durable store iff a DB path is set."""
    path = get_incident_config().ai_act_db_path
    if path:
        from core.incidents.persistence import SQLiteAiActIncidentStore

        return AiActIncidentService(store=SQLiteAiActIncidentStore(path))
    return AiActIncidentService()


def get_ai_act_incident_service() -> AiActIncidentService:
    """Get or create the global AI Act serious-incident service."""
    global _service
    if _service is None:
        _service = _build_ai_act_incident_service()
    return _service


def reset_ai_act_incident_service() -> None:
    """Drop the cached service (tests, and reconfiguration)."""
    global _service
    _service = None


__all__ = [
    "AiActIncidentNotFoundError",
    "AiActIncidentService",
    "AiActIncidentStore",
    "InMemoryAiActIncidentStore",
    "get_ai_act_incident_service",
    "reset_ai_act_incident_service",
]
