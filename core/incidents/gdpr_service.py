"""GDPR personal-data-breach service — Regulation (EU) 2016/679 Art. 33/34.

Maintains the **Art. 33(5) breach register** (every breach is documented,
notified or not), tracks the 72-hour Art. 33(1) clock toward the supervisory
authority and the Art. 34(1) communication to data subjects, and surfaces
overdue obligations. Every transition emits an ``AUDIT | GDPR-BREACH | …`` log
line plus a structured audit record.

Filing with the supervisory authority remains the controller's action — this
subsystem produces and tracks the structured record that backs it, and makes a
missed 72-hour deadline detectable instead of discovered at inspection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from core.config.incidents import IncidentReportingConfig, get_incident_config
from core.incidents.gdpr import (
    Art34Exemption,
    BreachRiskLevel,
    BreachRole,
    BreachStatus,
    PersonalDataBreach,
)
from core.incidents.types import ReportingMilestone, _utcnow
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)


class BreachNotFoundError(LookupError):
    """Raised when a breach id does not resolve to a stored record."""

    def __init__(self, breach_id: str) -> None:
        super().__init__(f"Personal data breach not found: {breach_id}")
        self.breach_id = breach_id


class BreachStore(Protocol):
    """Persistence boundary for the Art. 33(5) breach register."""

    async def save(self, breach: PersonalDataBreach) -> None:
        """Insert or update a breach record."""
        ...

    async def get(self, breach_id: str) -> PersonalDataBreach | None:
        """Fetch a breach by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> list[PersonalDataBreach]:
        """Return every stored breach."""
        ...


class InMemoryBreachStore:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._breaches: dict[str, PersonalDataBreach] = {}

    async def save(self, breach: PersonalDataBreach) -> None:
        self._breaches[breach.id] = breach

    async def get(self, breach_id: str) -> PersonalDataBreach | None:
        return self._breaches.get(breach_id)

    async def list_all(self) -> list[PersonalDataBreach]:
        return list(self._breaches.values())


def _rank(status: BreachStatus) -> int:
    """Order statuses so transitions only ever move forward."""
    order = {
        BreachStatus.DETECTED: 0,
        BreachStatus.AUTHORITY_NOTIFIED: 1,
        BreachStatus.SUBJECTS_COMMUNICATED: 2,
        BreachStatus.CLOSED: 3,
    }
    return order[status]


class BreachService:
    """Breach-register lifecycle and Art. 33/34 deadline tracking."""

    def __init__(
        self,
        store: BreachStore | None = None,
        config: IncidentReportingConfig | None = None,
    ) -> None:
        self._store = store or InMemoryBreachStore()
        self._config = config or get_incident_config()

    @property
    def store(self) -> BreachStore:
        return self._store

    async def record_breach(
        self,
        title: str,
        *,
        risk_level: BreachRiskLevel = BreachRiskLevel.RISK,
        role: BreachRole = BreachRole.CONTROLLER,
        description: str = "",
        data_categories: list[str] | None = None,
        affected_subjects: int = 0,
        affected_records: int = 0,
        likely_consequences: str = "",
        remedial_action: str = "",
        became_aware_at: datetime | None = None,
        details: dict[str, object] | None = None,
    ) -> PersonalDataBreach:
        """Enter a breach into the Art. 33(5) register.

        Every breach is registered, including ones assessed as ``NONE`` risk and
        therefore not notifiable — Art. 33(5) has no risk threshold.
        """
        breach = PersonalDataBreach(
            title=title,
            risk_level=risk_level,
            role=role,
            description=description,
            data_categories=list(data_categories or []),
            affected_subjects=affected_subjects,
            affected_records=affected_records,
            likely_consequences=likely_consequences,
            remedial_action=remedial_action,
            details=dict(details or {}),
        )
        if became_aware_at is not None:
            breach.became_aware_at = became_aware_at
        await self._store.save(breach)
        logger.info(
            "AUDIT | GDPR-BREACH | registered | id=%s risk=%s notify=%s subjects=%d",
            breach.id,
            breach.risk_level.value,
            breach.requires_authority_notification,
            breach.affected_subjects,
        )
        await get_audit_logger().log(
            AuditEventType.INCIDENT_OPEN,
            resource=breach.id,
            action="register",
            details={
                "regime": "gdpr",
                "risk_level": breach.risk_level.value,
                "role": breach.role.value,
                "requires_authority_notification": (
                    breach.requires_authority_notification
                ),
                "requires_subject_communication": (
                    breach.requires_subject_communication
                ),
                "affected_subjects": breach.affected_subjects,
            },
        )
        return breach

    async def _advance(
        self,
        breach_id: str,
        *,
        field_name: str,
        status: BreachStatus | None,
        at: datetime | None,
        milestone: str,
    ) -> PersonalDataBreach:
        """Stamp a milestone and advance status (never backwards)."""
        breach = await self._require(breach_id)
        stamp = at or _utcnow()
        setattr(breach, field_name, stamp)
        if status is not None and _rank(status) > _rank(breach.status):
            breach.status = status
        breach.updated_at = stamp
        await self._store.save(breach)
        logger.info(
            "AUDIT | GDPR-BREACH | %s | id=%s at=%s",
            milestone,
            breach.id,
            stamp.isoformat(),
        )
        await get_audit_logger().log(
            AuditEventType.INCIDENT_CLOSE
            if status is BreachStatus.CLOSED
            else AuditEventType.INCIDENT_MILESTONE,
            resource=breach.id,
            action=milestone,
            details={
                "regime": "gdpr",
                "milestone": milestone,
                "status": breach.status.value,
                "submitted_at": stamp.isoformat(),
                "late": breach.is_late,
            },
        )
        return breach

    async def notify_authority(
        self,
        breach_id: str,
        *,
        submitted_at: datetime | None = None,
        delay_reason: str | None = None,
    ) -> PersonalDataBreach:
        """Record the Art. 33(1) notification to the supervisory authority.

        A notification landing past 72 hours is lawful but must carry the
        reasons for the delay; when none is supplied the omission is logged, so
        the gap is visible before an inspection finds it.
        """
        breach = await self._require(breach_id)
        if delay_reason is not None:
            breach.delay_reason = delay_reason
            await self._store.save(breach)
        updated = await self._advance(
            breach_id,
            field_name="authority_notified_at",
            status=BreachStatus.AUTHORITY_NOTIFIED,
            at=submitted_at,
            milestone="authority_notification",
        )
        if updated.is_late and not updated.delay_reason:
            logger.warning(
                "AUDIT | GDPR-BREACH | late notification without a reason | id=%s "
                "(Art. 33(1) requires the reasons for the delay)",
                updated.id,
            )
        return updated

    async def notify_controller(
        self, breach_id: str, *, submitted_at: datetime | None = None
    ) -> PersonalDataBreach:
        """Record a processor's Art. 33(2) notification to its controller."""
        return await self._advance(
            breach_id,
            field_name="controller_notified_at",
            status=None,
            at=submitted_at,
            milestone="controller_notification",
        )

    async def communicate_to_subjects(
        self, breach_id: str, *, submitted_at: datetime | None = None
    ) -> PersonalDataBreach:
        """Record the Art. 34(1) communication to the data subjects."""
        return await self._advance(
            breach_id,
            field_name="subjects_communicated_at",
            status=BreachStatus.SUBJECTS_COMMUNICATED,
            at=submitted_at,
            milestone="subject_communication",
        )

    async def claim_exemption(
        self, breach_id: str, exemption: Art34Exemption, *, rationale: str = ""
    ) -> PersonalDataBreach:
        """Claim an Art. 34(3) ground for not communicating to data subjects.

        Recording the ground removes the communication milestone but keeps the
        claim in the register — the controller must be able to justify it.
        """
        breach = await self._require(breach_id)
        breach.subject_exemption = exemption
        if rationale:
            breach.details["art34_exemption_rationale"] = rationale
        breach.updated_at = _utcnow()
        await self._store.save(breach)
        logger.info(
            "AUDIT | GDPR-BREACH | art34 exemption | id=%s ground=%s",
            breach.id,
            exemption.value,
        )
        await get_audit_logger().log(
            AuditEventType.INCIDENT_MILESTONE,
            resource=breach.id,
            action="art34_exemption",
            details={
                "regime": "gdpr",
                "exemption": exemption.value,
                "rationale": rationale,
            },
        )
        return breach

    async def close_breach(
        self, breach_id: str, *, closed_at: datetime | None = None
    ) -> PersonalDataBreach:
        """Close a breach (obligations fulfilled or not applicable)."""
        return await self._advance(
            breach_id,
            field_name="closed_at",
            status=BreachStatus.CLOSED,
            at=closed_at,
            milestone="closed",
        )

    async def get(self, breach_id: str) -> PersonalDataBreach | None:
        """Fetch a breach by id."""
        return await self._store.get(breach_id)

    async def list_breaches(
        self, *, status: BreachStatus | None = None
    ) -> list[PersonalDataBreach]:
        """List register entries, optionally filtered by status."""
        breaches = await self._store.list_all()
        if status is not None:
            breaches = [b for b in breaches if b.status == status]
        return breaches

    async def list_open(self) -> list[PersonalDataBreach]:
        """List breaches that are not yet closed."""
        return [
            b for b in await self._store.list_all() if b.status != BreachStatus.CLOSED
        ]

    def milestones(self, breach: PersonalDataBreach) -> list[ReportingMilestone]:
        """Compute the Art. 33/34 milestones using the configured horizons."""
        return breach.milestones(
            authority_hours=self._config.gdpr_authority_notification_hours,
            subject_communication_hours=(
                self._config.gdpr_subject_communication_hours
            ),
        )

    async def overdue_milestones(
        self, now: datetime | None = None
    ) -> list[tuple[PersonalDataBreach, ReportingMilestone]]:
        """Return ``(breach, milestone)`` pairs with a missed, unmet deadline."""
        overdue: list[tuple[PersonalDataBreach, ReportingMilestone]] = []
        for breach in await self._store.list_all():
            if breach.status == BreachStatus.CLOSED:
                continue
            for milestone in self.milestones(breach):
                if milestone.is_overdue(now):
                    overdue.append((breach, milestone))
        return overdue

    async def _require(self, breach_id: str) -> PersonalDataBreach:
        breach = await self._store.get(breach_id)
        if breach is None:
            raise BreachNotFoundError(breach_id)
        return breach


_service: BreachService | None = None


def _build_breach_service() -> BreachService:
    """Build the service, selecting a durable store iff a DB path is set."""
    path = get_incident_config().gdpr_db_path
    if path:
        from core.incidents.persistence import SQLiteBreachStore

        return BreachService(store=SQLiteBreachStore(path))
    return BreachService()


def get_breach_service() -> BreachService:
    """Get or create the global GDPR breach-register service."""
    global _service
    if _service is None:
        _service = _build_breach_service()
    return _service


def reset_breach_service() -> None:
    """Drop the cached service (tests, and reconfiguration)."""
    global _service
    _service = None


__all__ = [
    "BreachNotFoundError",
    "BreachService",
    "BreachStore",
    "InMemoryBreachStore",
    "get_breach_service",
    "reset_breach_service",
]
