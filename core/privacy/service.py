"""
Data-subject service — orchestrates export, erasure, and retention.

Aggregates every registered :class:`~core.privacy.provider.DataProvider`. Each
operation emits an audit log line (``AUDIT | PRIVACY | …``) so data-subject
requests are traceable for compliance. Providers are isolated: one failing
provider is recorded and does not abort the others.
"""

from __future__ import annotations

import time
from typing import Any

from core.observability.audit import AuditEventType, get_audit_logger
from core.observability.logging import get_logger
from core.privacy.provider import (
    DataProviderRegistry,
    RectificationProvider,
    RestrictionProvider,
    RetentionProvider,
)
from core.privacy.types import (
    ErasureReport,
    ObjectionOutcome,
    ObjectionRecord,
    RectificationReport,
    RestrictionReport,
    RetentionReport,
    SubjectExport,
)

logger = get_logger(__name__)


class DataSubjectService:
    """Export, erase, and apply retention across all data providers."""

    def __init__(self, registry: DataProviderRegistry | None = None) -> None:
        self._registry = registry or DataProviderRegistry()

    @property
    def registry(self) -> DataProviderRegistry:
        return self._registry

    async def export_subject(self, subject_id: str) -> SubjectExport:
        """Aggregate every provider's data for ``subject_id`` (right to access)."""
        bundle = SubjectExport(subject_id=subject_id)
        for provider in self._registry.all():
            try:
                bundle.data[provider.name] = await provider.export(subject_id)
            except Exception as exc:
                logger.error(
                    "privacy_export_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                bundle.data[provider.name] = {"error": "export_failed"}
        logger.info(
            "AUDIT | PRIVACY | subject export | subject=%s providers=%d",
            subject_id,
            len(bundle.data),
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_EXPORT,
            resource=subject_id,
            action="export",
            details={"providers": sorted(bundle.data)},
        )
        return bundle

    async def erase_subject(self, subject_id: str) -> ErasureReport:
        """Erase ``subject_id`` from every provider (right to erasure)."""
        report = ErasureReport(subject_id=subject_id)
        for provider in self._registry.all():
            try:
                report.erased[provider.name] = await provider.erase(subject_id)
            except Exception as exc:
                logger.error(
                    "privacy_erase_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                report.erased[provider.name] = 0
        logger.info(
            "AUDIT | PRIVACY | subject erasure | subject=%s removed=%d",
            subject_id,
            report.total,
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_ERASE,
            resource=subject_id,
            action="erase",
            details={"erased": dict(report.erased), "total": report.total},
        )
        return report

    async def rectify_subject(
        self, subject_id: str, corrections: dict[str, Any]
    ) -> RectificationReport:
        """Correct inaccurate personal data across providers (Art. 16).

        Providers that hold data but cannot rectify it are reported in
        ``unsupported`` rather than skipped silently: Art. 19 obliges the
        controller to communicate a rectification to each recipient, which it
        cannot do for a store it does not know failed.
        """
        report = RectificationReport(subject_id=subject_id, corrections=corrections)
        for provider in self._registry.all():
            if not isinstance(provider, RectificationProvider):
                report.unsupported.append(provider.name)
                continue
            try:
                report.rectified[provider.name] = await provider.rectify(
                    subject_id, corrections
                )
            except Exception as exc:
                logger.error(
                    "privacy_rectify_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                report.rectified[provider.name] = 0
        logger.info(
            "AUDIT | PRIVACY | subject rectification | subject=%s changed=%d unsupported=%d",
            subject_id,
            report.total,
            len(report.unsupported),
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_RECTIFY,
            resource=subject_id,
            action="rectify",
            details={
                "fields": sorted(corrections),
                "rectified": dict(report.rectified),
                "unsupported": report.unsupported,
            },
        )
        return report

    async def restrict_subject(
        self, subject_id: str, *, restricted: bool = True
    ) -> RestrictionReport:
        """Restrict (or release) processing for a subject (Art. 18).

        Restriction is not erasure — the data stays, but providers that honour
        the flag must stop processing beyond storage.
        """
        report = RestrictionReport(subject_id=subject_id, restricted=restricted)
        for provider in self._registry.all():
            if not isinstance(provider, RestrictionProvider):
                report.unsupported.append(provider.name)
                continue
            try:
                report.affected[provider.name] = await provider.restrict(
                    subject_id, restricted
                )
            except Exception as exc:
                logger.error(
                    "privacy_restrict_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
                report.affected[provider.name] = 0
        logger.info(
            "AUDIT | PRIVACY | subject restriction | subject=%s restricted=%s affected=%d",
            subject_id,
            restricted,
            report.total,
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_RESTRICT,
            resource=subject_id,
            action="restrict" if restricted else "release",
            details={
                "restricted": restricted,
                "affected": dict(report.affected),
                "unsupported": report.unsupported,
            },
        )
        return report

    async def record_objection(
        self,
        subject_id: str,
        *,
        processing: str = "",
        direct_marketing: bool = False,
        override_grounds: str | None = None,
    ) -> ObjectionRecord:
        """Record an Art. 21 objection and apply its outcome.

        An objection to **direct marketing** (Art. 21(2)/(3)) is absolute: it is
        always upheld and processing is restricted, whatever grounds are passed.
        For other processing, ``override_grounds`` may be supplied to record the
        compelling legitimate grounds of Art. 21(1); without them the objection
        is upheld and processing restricted.
        """
        absolute = direct_marketing
        upheld = absolute or not override_grounds
        record = ObjectionRecord(
            subject_id=subject_id,
            processing=processing,
            direct_marketing=direct_marketing,
            outcome=(
                ObjectionOutcome.UPHELD if upheld else ObjectionOutcome.OVERRIDDEN
            ),
            override_grounds=None if absolute else override_grounds,
        )
        if absolute and override_grounds:
            logger.warning(
                "AUDIT | PRIVACY | objection override ignored | subject=%s "
                "(Art. 21(2)/(3): a direct-marketing objection admits no override)",
                subject_id,
            )
        if upheld:
            record.restriction = await self.restrict_subject(subject_id)
        record.resolved_at = time.time()
        logger.info(
            "AUDIT | PRIVACY | subject objection | subject=%s outcome=%s marketing=%s",
            subject_id,
            record.outcome.value,
            direct_marketing,
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_OBJECT,
            resource=subject_id,
            action="object",
            success=upheld,
            details={
                "processing": processing,
                "direct_marketing": direct_marketing,
                "outcome": record.outcome.value,
                "override_grounds": record.override_grounds,
            },
        )
        return record

    async def purge_expired(self, older_than_seconds: int) -> RetentionReport:
        """Run a retention sweep across providers that support purging."""
        report = RetentionReport(older_than_seconds=older_than_seconds)
        for provider in self._registry.all():
            if not isinstance(provider, RetentionProvider):
                continue
            try:
                report.purged[provider.name] = await provider.purge_expired(
                    older_than_seconds
                )
            except Exception as exc:
                logger.error(
                    "privacy_purge_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
        logger.info(
            "AUDIT | PRIVACY | retention sweep | older_than=%ds purged=%d",
            older_than_seconds,
            report.total,
        )
        await get_audit_logger().log(
            AuditEventType.PRIVACY_RETENTION,
            action="retention_sweep",
            details={
                "older_than_seconds": older_than_seconds,
                "purged": dict(report.purged),
                "total": report.total,
            },
        )
        return report


_registry = DataProviderRegistry()
_service: DataSubjectService | None = None


def register_data_provider(provider) -> None:  # type: ignore[no-untyped-def]
    """Register a global data provider (subsystems call this at startup)."""
    _registry.register(provider)


def get_data_subject_service() -> DataSubjectService:
    """Get or create the global data-subject service over the shared registry."""
    global _service
    if _service is None:
        _service = DataSubjectService(_registry)
    return _service
