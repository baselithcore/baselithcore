"""
Privacy / data-subject-request (DSR) framework.

Aggregates personal data across registered providers to satisfy GDPR access,
portability, erasure, and retention. Subsystems register a
:class:`~core.privacy.provider.DataProvider`; the
:class:`~core.privacy.service.DataSubjectService` does the rest.
"""

from core.privacy.postgres import PostgresDataProvider
from core.privacy.provider import (
    DataProvider,
    DataProviderRegistry,
    DictDataProvider,
    RetentionProvider,
)
from core.privacy.scheduler import RetentionScheduler
from core.privacy.service import (
    DataSubjectService,
    get_data_subject_service,
    register_data_provider,
)
from core.privacy.types import (
    ErasureReport,
    PrivacyError,
    RetentionReport,
    SubjectExport,
)

__all__ = [
    "DataProvider",
    "DataProviderRegistry",
    "DataSubjectService",
    "DictDataProvider",
    "ErasureReport",
    "PostgresDataProvider",
    "PrivacyError",
    "RetentionProvider",
    "RetentionReport",
    "RetentionScheduler",
    "SubjectExport",
    "get_data_subject_service",
    "register_data_provider",
]
