"""
Privacy / data-subject-request (DSR) framework.

Aggregates personal data across registered providers to satisfy the GDPR
Chapter III rights — access and portability (Art. 15/20), rectification
(Art. 16), erasure (Art. 17), restriction (Art. 18) and objection (Art. 21) —
plus storage limitation (Art. 5(1)(e)) and consent proof (Art. 7).

Subsystems register a :class:`~core.privacy.provider.DataProvider`; the
:class:`~core.privacy.service.DataSubjectService` does the rest. Rectification
and restriction are *optional extensions* checked at runtime, so an existing
provider keeps working unchanged and simply reports as unsupported for those
rights instead of failing.
"""

from core.privacy.automated_decisions import (
    Art22Ground,
    AutomatedDecisionActivity,
    AutomatedDecisionRegistry,
    get_automated_decision_registry,
    reset_automated_decision_registry,
)
from core.privacy.consent import (
    ConsentRecord,
    ConsentService,
    ConsentStore,
    InMemoryConsentStore,
    SQLiteConsentStore,
    get_consent_service,
    reset_consent_service,
)
from core.privacy.postgres import PostgresDataProvider
from core.privacy.provider import (
    DataProvider,
    DataProviderRegistry,
    DictDataProvider,
    RectificationProvider,
    RestrictionProvider,
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
    ObjectionOutcome,
    ObjectionRecord,
    PrivacyError,
    RectificationReport,
    RestrictionReport,
    RetentionReport,
    SubjectExport,
)

__all__ = [
    "Art22Ground",
    "AutomatedDecisionActivity",
    "AutomatedDecisionRegistry",
    "ConsentRecord",
    "ConsentService",
    "ConsentStore",
    "DataProvider",
    "DataProviderRegistry",
    "DataSubjectService",
    "DictDataProvider",
    "ErasureReport",
    "InMemoryConsentStore",
    "ObjectionOutcome",
    "ObjectionRecord",
    "PostgresDataProvider",
    "PrivacyError",
    "RectificationProvider",
    "RectificationReport",
    "RestrictionProvider",
    "RestrictionReport",
    "RetentionProvider",
    "RetentionReport",
    "RetentionScheduler",
    "SQLiteConsentStore",
    "SubjectExport",
    "get_automated_decision_registry",
    "get_consent_service",
    "get_data_subject_service",
    "register_data_provider",
    "reset_automated_decision_registry",
    "reset_consent_service",
]
