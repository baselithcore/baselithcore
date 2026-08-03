"""Compliance document services — Annex IV, FRIA, and the Art. 30 ROPA.

Three artefacts, one shape: a registry of long-lived records whose *complete-
ness* is the thing worth checking automatically. Each service stores records,
audits every write, and can report which statutory elements are still missing —
so "we have a FRIA" becomes a claim the code can substantiate or contradict.

Substance remains human work. What these services remove is the failure mode
where a document exists, nobody re-reads it, and a required element was never
filled in.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from core.compliance.annex_iv import AnnexIVSection, TechnicalDocumentation
from core.compliance.fria import FundamentalRightsImpactAssessment
from core.compliance.ropa import ProcessingActivity
from core.compliance.types import _utcnow
from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class RecordStore(Protocol[T]):
    """Persistence boundary shared by the three document registries."""

    async def save(self, record: T) -> None:
        """Insert or update a record."""
        ...

    async def get(self, record_id: str) -> T | None:
        """Fetch a record by id, or ``None`` if unknown."""
        ...

    async def list_all(self) -> list[T]:
        """Return every stored record."""
        ...

    async def delete(self, record_id: str) -> bool:
        """Remove a record; returns whether anything was removed."""
        ...


class InMemoryRecordStore[T]:
    """Reference in-memory store (non-durable; tests/single-process)."""

    def __init__(self) -> None:
        self._records: dict[str, T] = {}

    async def save(self, record: T) -> None:
        self._records[getattr(record, "id")] = record  # noqa: B009

    async def get(self, record_id: str) -> T | None:
        return self._records.get(record_id)

    async def list_all(self) -> list[T]:
        return list(self._records.values())

    async def delete(self, record_id: str) -> bool:
        return self._records.pop(record_id, None) is not None


class TechnicalDocumentationService:
    """Art. 11 / Annex IV technical documentation registry."""

    def __init__(
        self, store: RecordStore[TechnicalDocumentation] | None = None
    ) -> None:
        self._store = store or InMemoryRecordStore[TechnicalDocumentation]()

    async def save(self, document: TechnicalDocumentation) -> TechnicalDocumentation:
        """Store a document and audit the write."""
        document.updated_at = _utcnow()
        await self._store.save(document)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=document.system_id,
            action="annex_iv_documentation",
            success=document.is_complete,
            details={
                "document_id": document.id,
                "version": document.version,
                "missing_sections": [s.value for s in document.missing_sections()],
            },
        )
        return document

    async def set_section(
        self, document_id: str, section: AnnexIVSection, content: str
    ) -> TechnicalDocumentation:
        """Write one Annex IV section and re-audit completeness."""
        document = await self.require(document_id)
        document.set_section(section, content)
        return await self.save(document)

    async def approve(
        self, document_id: str, approved_by: str
    ) -> TechnicalDocumentation:
        """Record sign-off. Approving an incomplete document is logged as such."""
        document = await self.require(document_id)
        document.approved_by = approved_by
        document.approved_at = _utcnow()
        if not document.is_complete:
            logger.warning(
                "AUDIT | COMPLIANCE | Annex IV approved while incomplete | "
                "document=%s missing=%s",
                document.id,
                [s.value for s in document.missing_sections()],
            )
        return await self.save(document)

    async def get(self, document_id: str) -> TechnicalDocumentation | None:
        return await self._store.get(document_id)

    async def require(self, document_id: str) -> TechnicalDocumentation:
        document = await self._store.get(document_id)
        if document is None:
            raise LookupError(f"Technical documentation not found: {document_id}")
        return document

    async def list_documents(self) -> list[TechnicalDocumentation]:
        """Every stored document."""
        return await self._store.list_all()

    async def for_system(self, system_id: str) -> list[TechnicalDocumentation]:
        """Every document version recorded for one AI system."""
        return [d for d in await self._store.list_all() if d.system_id == system_id]

    async def incomplete(self) -> list[TechnicalDocumentation]:
        """Documents with at least one empty Annex IV section."""
        return [d for d in await self._store.list_all() if not d.is_complete]


class FriaService:
    """Art. 27 fundamental rights impact assessment registry."""

    def __init__(
        self, store: RecordStore[FundamentalRightsImpactAssessment] | None = None
    ) -> None:
        self._store = store or InMemoryRecordStore[FundamentalRightsImpactAssessment]()

    async def save(
        self, assessment: FundamentalRightsImpactAssessment
    ) -> FundamentalRightsImpactAssessment:
        """Store an assessment and audit its completeness."""
        assessment.updated_at = _utcnow()
        await self._store.save(assessment)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=assessment.system_id,
            action="fria",
            success=assessment.is_complete,
            details={
                "assessment_id": assessment.id,
                "deployer": assessment.deployer,
                "missing_elements": assessment.missing_elements(),
            },
        )
        return assessment

    async def complete(self, assessment_id: str) -> FundamentalRightsImpactAssessment:
        """Mark an assessment complete, refusing to do so while elements are missing.

        Art. 27(1) lists what a FRIA *shall* contain; stamping one complete with
        statutory elements empty would put a false claim in the record.
        """
        assessment = await self.require(assessment_id)
        missing = assessment.missing_elements()
        if missing:
            raise ValueError(
                "FRIA is missing required Art. 27(1) elements: " + "; ".join(missing)
            )
        assessment.completed_at = _utcnow()
        return await self.save(assessment)

    async def notify_authority(
        self, assessment_id: str
    ) -> FundamentalRightsImpactAssessment:
        """Record the Art. 27(3) notification of the results to the authority."""
        assessment = await self.require(assessment_id)
        assessment.authority_notified_at = _utcnow()
        return await self.save(assessment)

    async def get(self, assessment_id: str) -> FundamentalRightsImpactAssessment | None:
        return await self._store.get(assessment_id)

    async def require(self, assessment_id: str) -> FundamentalRightsImpactAssessment:
        assessment = await self._store.get(assessment_id)
        if assessment is None:
            raise LookupError(f"FRIA not found: {assessment_id}")
        return assessment

    async def list_assessments(self) -> list[FundamentalRightsImpactAssessment]:
        """Every stored assessment."""
        return await self._store.list_all()

    async def for_system(
        self, system_id: str
    ) -> list[FundamentalRightsImpactAssessment]:
        """Every assessment recorded for one AI system."""
        return [a for a in await self._store.list_all() if a.system_id == system_id]

    async def incomplete(self) -> list[FundamentalRightsImpactAssessment]:
        """Assessments with at least one empty Art. 27(1) element."""
        return [a for a in await self._store.list_all() if not a.is_complete]


class RopaService:
    """GDPR Art. 30 register of processing activities."""

    def __init__(self, store: RecordStore[ProcessingActivity] | None = None) -> None:
        self._store = store or InMemoryRecordStore[ProcessingActivity]()

    async def save(self, activity: ProcessingActivity) -> ProcessingActivity:
        """Store a register entry and audit its completeness."""
        activity.updated_at = _utcnow()
        await self._store.save(activity)
        await get_audit_logger().log(
            AuditEventType.COMPLIANCE_ASSESSMENT,
            resource=activity.id,
            action="ropa_entry",
            success=activity.is_complete,
            details={
                "name": activity.name,
                "role": activity.role.value,
                "missing_elements": activity.missing_elements(),
            },
        )
        return activity

    async def review(self, activity_id: str) -> ProcessingActivity:
        """Stamp a periodic review — a ROPA nobody revisits goes stale silently."""
        activity = await self.require(activity_id)
        activity.reviewed_at = _utcnow()
        return await self.save(activity)

    async def get(self, activity_id: str) -> ProcessingActivity | None:
        return await self._store.get(activity_id)

    async def require(self, activity_id: str) -> ProcessingActivity:
        activity = await self._store.get(activity_id)
        if activity is None:
            raise LookupError(f"Processing activity not found: {activity_id}")
        return activity

    async def list_activities(self) -> list[ProcessingActivity]:
        """The full register."""
        return await self._store.list_all()

    async def for_system(self, system_id: str) -> list[ProcessingActivity]:
        """Entries whose processing feeds a given AI system."""
        return [a for a in await self._store.list_all() if a.ai_system_id == system_id]

    async def incomplete(self) -> list[ProcessingActivity]:
        """Entries with at least one empty Art. 30 element."""
        return [a for a in await self._store.list_all() if not a.is_complete]


_docs_service: TechnicalDocumentationService | None = None
_fria_service: FriaService | None = None
_ropa_service: RopaService | None = None


def get_technical_documentation_service() -> TechnicalDocumentationService:
    """Get or create the global Annex IV documentation service."""
    global _docs_service
    if _docs_service is None:
        from core.config.compliance import get_compliance_config

        path = get_compliance_config().documents_db_path
        if path:
            from core.compliance.persistence import (
                SQLiteTechnicalDocumentationStore,
            )

            _docs_service = TechnicalDocumentationService(
                store=SQLiteTechnicalDocumentationStore(path)
            )
        else:
            _docs_service = TechnicalDocumentationService()
    return _docs_service


def get_fria_service() -> FriaService:
    """Get or create the global FRIA service."""
    global _fria_service
    if _fria_service is None:
        from core.config.compliance import get_compliance_config

        path = get_compliance_config().fria_db_path
        if path:
            from core.compliance.persistence import SQLiteFriaStore

            _fria_service = FriaService(store=SQLiteFriaStore(path))
        else:
            _fria_service = FriaService()
    return _fria_service


def get_ropa_service() -> RopaService:
    """Get or create the global ROPA service."""
    global _ropa_service
    if _ropa_service is None:
        from core.config.compliance import get_compliance_config

        path = get_compliance_config().ropa_db_path
        if path:
            from core.compliance.persistence import SQLiteRopaStore

            _ropa_service = RopaService(store=SQLiteRopaStore(path))
        else:
            _ropa_service = RopaService()
    return _ropa_service


def reset_document_services() -> None:
    """Drop the cached services (tests, and reconfiguration)."""
    global _docs_service, _fria_service, _ropa_service
    _docs_service = None
    _fria_service = None
    _ropa_service = None


__all__ = [
    "FriaService",
    "InMemoryRecordStore",
    "RecordStore",
    "RopaService",
    "TechnicalDocumentationService",
    "get_fria_service",
    "get_ropa_service",
    "get_technical_documentation_service",
    "reset_document_services",
]
