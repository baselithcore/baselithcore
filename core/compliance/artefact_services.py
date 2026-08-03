"""Services for the Art. 9 risk file, Art. 13 instructions and the GDPR DPIA.

Same shape as :mod:`core.compliance.documents`: store, audit every write, and
report which statutory elements are still missing. Two of these carry a *gate*
rather than a warning, because the underlying article makes an incomplete
artefact unlawful rather than merely untidy:

* :meth:`RiskManagementService.review` refuses to stamp a review on a file with
  open risks — Art. 9(1) asks for a systematic review, and signing one over
  untreated risks records something that did not happen;
* :meth:`DpiaService.record_prior_consultation` is what unlocks processing when
  residual risk stays high (Art. 36(1)); until it exists,
  ``may_start_processing`` stays false.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.compliance.documents import InMemoryRecordStore, RecordStore
from core.compliance.dpia import DataProtectionImpactAssessment
from core.compliance.instructions import InstructionsForUse
from core.compliance.risk_management import RiskManagementSystem
from core.compliance.types import _utcnow
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)


class RiskManagementService:
    """Art. 9 risk management files."""

    def __init__(self, store: RecordStore[RiskManagementSystem] | None = None) -> None:
        self._store = store or InMemoryRecordStore[RiskManagementSystem]()

    async def save(self, file: RiskManagementSystem) -> RiskManagementSystem:
        """Store a risk file and audit its completeness."""
        file.updated_at = _utcnow()
        await self._store.save(file)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=file.system_id,
            action="risk_management",
            success=file.is_complete,
            details={
                "file_id": file.id,
                "risks": len(file.risks),
                "open_risks": len(file.open_risks),
                "missing_elements": file.missing_elements(),
            },
        )
        return file

    async def review(
        self, file_id: str, *, at: datetime | None = None
    ) -> RiskManagementSystem:
        """Record a systematic review (Art. 9(1)).

        Raises when risks are still open: a review recorded over untreated,
        unverified or unaccepted risks asserts something that did not happen.
        """
        file = await self.require(file_id)
        if file.open_risks:
            descriptions = ", ".join(r.description for r in file.open_risks)
            raise ValueError(
                "Cannot record an Art. 9(1) review while risks remain open: "
                f"{descriptions}"
            )
        file.last_reviewed_at = at or _utcnow()
        return await self.save(file)

    async def get(self, file_id: str) -> RiskManagementSystem | None:
        return await self._store.get(file_id)

    async def require(self, file_id: str) -> RiskManagementSystem:
        file = await self._store.get(file_id)
        if file is None:
            raise LookupError(f"Risk management file not found: {file_id}")
        return file

    async def for_system(self, system_id: str) -> list[RiskManagementSystem]:
        """Every risk file recorded for one AI system."""
        return [f for f in await self._store.list_all() if f.system_id == system_id]

    async def list_files(self) -> list[RiskManagementSystem]:
        return await self._store.list_all()

    async def incomplete(self) -> list[RiskManagementSystem]:
        """Files with at least one missing Art. 9 element or open risk."""
        return [f for f in await self._store.list_all() if not f.is_complete]

    async def overdue_reviews(
        self, now: datetime | None = None
    ) -> list[RiskManagementSystem]:
        """Files past their review cadence, never-reviewed ones included."""
        return [f for f in await self._store.list_all() if f.is_review_overdue(now)]


class InstructionsService:
    """Art. 13 instructions for use."""

    def __init__(self, store: RecordStore[InstructionsForUse] | None = None) -> None:
        self._store = store or InMemoryRecordStore[InstructionsForUse]()

    async def save(self, instructions: InstructionsForUse) -> InstructionsForUse:
        """Store instructions and audit their completeness."""
        instructions.updated_at = _utcnow()
        await self._store.save(instructions)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=instructions.system_id,
            action="instructions_for_use",
            success=instructions.is_complete,
            details={
                "instructions_id": instructions.id,
                "version": instructions.version,
                "missing_elements": instructions.missing_elements(),
            },
        )
        return instructions

    async def issue(self, instructions_id: str) -> InstructionsForUse:
        """Mark instructions as issued to deployers.

        Refuses while Art. 13(3) elements are missing: instructions that omit
        the limitations or the oversight measures leave the deployer unable to
        perform its own Art. 26 duties, which is the harm Art. 13 addresses.
        """
        instructions = await self.require(instructions_id)
        missing = instructions.missing_elements()
        if missing:
            raise ValueError(
                "Instructions are missing required Art. 13(3) elements: "
                + "; ".join(missing)
            )
        instructions.issued_at = _utcnow()
        return await self.save(instructions)

    async def get(self, instructions_id: str) -> InstructionsForUse | None:
        return await self._store.get(instructions_id)

    async def require(self, instructions_id: str) -> InstructionsForUse:
        instructions = await self._store.get(instructions_id)
        if instructions is None:
            raise LookupError(f"Instructions for use not found: {instructions_id}")
        return instructions

    async def for_system(self, system_id: str) -> list[InstructionsForUse]:
        """Every version of the instructions recorded for one AI system."""
        return [i for i in await self._store.list_all() if i.system_id == system_id]

    async def list_instructions(self) -> list[InstructionsForUse]:
        return await self._store.list_all()

    async def incomplete(self) -> list[InstructionsForUse]:
        """Instructions with at least one empty Art. 13(3) element."""
        return [i for i in await self._store.list_all() if not i.is_complete]


class DpiaService:
    """GDPR Art. 35/36 data protection impact assessments."""

    def __init__(
        self, store: RecordStore[DataProtectionImpactAssessment] | None = None
    ) -> None:
        self._store = store or InMemoryRecordStore[
            DataProtectionImpactAssessment
        ]()

    async def save(
        self, assessment: DataProtectionImpactAssessment
    ) -> DataProtectionImpactAssessment:
        """Store an assessment and audit its completeness."""
        assessment.updated_at = _utcnow()
        await self._store.save(assessment)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=assessment.id,
            action="dpia",
            success=assessment.is_complete,
            details={
                "name": assessment.name,
                "triggers": [t.value for t in assessment.triggers],
                "residual_high_risk": assessment.has_residual_high_risk,
                "requires_prior_consultation": (
                    assessment.requires_prior_consultation
                ),
                "missing_elements": assessment.missing_elements(),
            },
        )
        return assessment

    async def complete(self, assessment_id: str) -> DataProtectionImpactAssessment:
        """Mark an assessment complete, refusing while elements are missing.

        When residual risk stays high, completing does **not** unlock
        processing: Art. 36(1) prior consultation is still owed, and
        ``may_start_processing`` stays false until it is recorded. The gap is
        logged so it cannot be mistaken for a green light.
        """
        assessment = await self.require(assessment_id)
        missing = assessment.missing_elements()
        if missing:
            raise ValueError(
                "DPIA is missing required Art. 35(7) elements: " + "; ".join(missing)
            )
        assessment.completed_at = _utcnow()
        saved = await self.save(assessment)
        if saved.requires_prior_consultation:
            logger.warning(
                "AUDIT | COMPLIANCE | DPIA complete with high residual risk | "
                "dpia=%s — Art. 36(1) prior consultation is required BEFORE "
                "processing starts",
                saved.id,
            )
        return saved

    async def record_prior_consultation(
        self, assessment_id: str, *, at: datetime | None = None
    ) -> DataProtectionImpactAssessment:
        """Record the Art. 36(1) consultation of the supervisory authority."""
        assessment = await self.require(assessment_id)
        assessment.prior_consultation_at = at or _utcnow()
        return await self.save(assessment)

    async def record_authority_response(
        self, assessment_id: str, *, at: datetime | None = None
    ) -> DataProtectionImpactAssessment:
        """Record the authority's response to the prior consultation."""
        assessment = await self.require(assessment_id)
        assessment.authority_response_at = at or _utcnow()
        return await self.save(assessment)

    async def review(
        self, assessment_id: str, *, at: datetime | None = None
    ) -> DataProtectionImpactAssessment:
        """Record the Art. 35(11) review that keeps the assessment current."""
        assessment = await self.require(assessment_id)
        assessment.last_reviewed_at = at or _utcnow()
        return await self.save(assessment)

    async def get(
        self, assessment_id: str
    ) -> DataProtectionImpactAssessment | None:
        return await self._store.get(assessment_id)

    async def require(
        self, assessment_id: str
    ) -> DataProtectionImpactAssessment:
        assessment = await self._store.get(assessment_id)
        if assessment is None:
            raise LookupError(f"DPIA not found: {assessment_id}")
        return assessment

    async def for_system(
        self, system_id: str
    ) -> list[DataProtectionImpactAssessment]:
        """Every assessment covering one AI system."""
        return [
            a for a in await self._store.list_all() if a.ai_system_id == system_id
        ]

    async def list_assessments(self) -> list[DataProtectionImpactAssessment]:
        return await self._store.list_all()

    async def incomplete(self) -> list[DataProtectionImpactAssessment]:
        """Assessments with at least one empty Art. 35(7) element."""
        return [a for a in await self._store.list_all() if not a.is_complete]

    async def blocked(self) -> list[DataProtectionImpactAssessment]:
        """Assessments whose processing may not lawfully start yet.

        Either incomplete, or carrying a high residual risk with no Art. 36(1)
        prior consultation on record.
        """
        return [
            a for a in await self._store.list_all() if not a.may_start_processing
        ]


_risk_service: RiskManagementService | None = None
_instructions_service: InstructionsService | None = None
_dpia_service: DpiaService | None = None


def _store_for(attribute: str, store_class: str) -> Any:
    """Build a durable store from config, or ``None`` for the in-memory default."""
    from core.config.compliance import get_compliance_config

    path = getattr(get_compliance_config(), attribute, None)
    if not path:
        return None
    from core.compliance import persistence

    return getattr(persistence, store_class)(path)


def get_risk_management_service() -> RiskManagementService:
    """Get or create the global Art. 9 risk management service."""
    global _risk_service
    if _risk_service is None:
        _risk_service = RiskManagementService(
            store=_store_for("risk_db_path", "SQLiteRiskManagementStore")
        )
    return _risk_service


def get_instructions_service() -> InstructionsService:
    """Get or create the global Art. 13 instructions service."""
    global _instructions_service
    if _instructions_service is None:
        _instructions_service = InstructionsService(
            store=_store_for("instructions_db_path", "SQLiteInstructionsStore")
        )
    return _instructions_service


def get_dpia_service() -> DpiaService:
    """Get or create the global GDPR DPIA service."""
    global _dpia_service
    if _dpia_service is None:
        _dpia_service = DpiaService(
            store=_store_for("dpia_db_path", "SQLiteDpiaStore")
        )
    return _dpia_service


def reset_artefact_services() -> None:
    """Drop the cached services (tests, and reconfiguration)."""
    global _risk_service, _instructions_service, _dpia_service
    _risk_service = None
    _instructions_service = None
    _dpia_service = None


__all__ = [
    "DpiaService",
    "InstructionsService",
    "RiskManagementService",
    "get_dpia_service",
    "get_instructions_service",
    "get_risk_management_service",
    "reset_artefact_services",
]
