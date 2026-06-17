"""
Data-subject service — orchestrates export, erasure, and retention.

Aggregates every registered :class:`~core.privacy.provider.DataProvider`. Each
operation emits an audit log line (``AUDIT | PRIVACY | …``) so data-subject
requests are traceable for compliance. Providers are isolated: one failing
provider is recorded and does not abort the others.
"""

from __future__ import annotations

from typing import Optional

from core.observability.logging import get_logger
from core.privacy.provider import DataProviderRegistry, RetentionProvider
from core.privacy.types import ErasureReport, RetentionReport, SubjectExport

logger = get_logger(__name__)


class DataSubjectService:
    """Export, erase, and apply retention across all data providers."""

    def __init__(self, registry: Optional[DataProviderRegistry] = None) -> None:
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
            except Exception as exc:  # noqa: BLE001 — isolate provider failures
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
        return bundle

    async def erase_subject(self, subject_id: str) -> ErasureReport:
        """Erase ``subject_id`` from every provider (right to erasure)."""
        report = ErasureReport(subject_id=subject_id)
        for provider in self._registry.all():
            try:
                report.erased[provider.name] = await provider.erase(subject_id)
            except Exception as exc:  # noqa: BLE001 — isolate provider failures
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
        return report

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
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "privacy_purge_provider_failed",
                    extra={"provider": provider.name, "error": str(exc)},
                )
        logger.info(
            "AUDIT | PRIVACY | retention sweep | older_than=%ds purged=%d",
            older_than_seconds,
            report.total,
        )
        return report


_registry = DataProviderRegistry()
_service: Optional[DataSubjectService] = None


def register_data_provider(provider) -> None:  # type: ignore[no-untyped-def]
    """Register a global data provider (subsystems call this at startup)."""
    _registry.register(provider)


def get_data_subject_service() -> DataSubjectService:
    """Get or create the global data-subject service over the shared registry."""
    global _service
    if _service is None:
        _service = DataSubjectService(_registry)
    return _service
