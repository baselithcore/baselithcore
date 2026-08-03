"""AI-governance subsystem — the AI Act obligations that attach to a *system*.

The incident and transparency subsystems cover events and disclosures. This one
covers the thing they attach to: **which AI systems do we operate, in which risk
category, under which role** — and the governance artefacts that follow from
that answer.

| Module | Regulatory anchor |
| ------ | ----------------- |
| :mod:`~core.compliance.types` | Art. 3 roles, Art. 6 risk tiers, Annex I/III, conformity record |
| :mod:`~core.compliance.prohibited` | Art. 5 prohibited practices, screened and audited |
| :mod:`~core.compliance.classification` | Art. 6 classification with the Art. 6(3) derogation logic |
| :mod:`~core.compliance.registry` | The inventory, Art. 49 registration tracking |
| :mod:`~core.compliance.annex_iv` | Art. 11 + Annex IV technical documentation |
| :mod:`~core.compliance.fria` | Art. 27 fundamental rights impact assessment |
| :mod:`~core.compliance.ropa` | GDPR Art. 30 records of processing activities |
| :mod:`~core.compliance.post_market` | Art. 72 post-market monitoring plan |

Opt-in via ``COMPLIANCE_ENABLED``; domain-agnostic infrastructure, so it lives
in the Sacred Core.
"""

from core.compliance.annex_iv import (
    AnnexIVSection,
    TechnicalDocumentation,
    draft_from_system,
)
from core.compliance.artefact_services import (
    DpiaService,
    InstructionsService,
    RiskManagementService,
    get_dpia_service,
    get_instructions_service,
    get_risk_management_service,
    reset_artefact_services,
)
from core.compliance.classification import (
    ClassificationResult,
    classify_system,
    obligations_for,
)
from core.compliance.documents import (
    FriaService,
    RopaService,
    TechnicalDocumentationService,
    get_fria_service,
    get_ropa_service,
    get_technical_documentation_service,
    reset_document_services,
)
from core.compliance.dpia import (
    DataProtectionImpactAssessment,
    DpiaRisk,
    DpiaTrigger,
)
from core.compliance.fria import FriaRisk, FundamentalRightsImpactAssessment
from core.compliance.instructions import InstructionsForUse, draft_instructions
from core.compliance.post_market import (
    MonitoringMetric,
    PostMarketMonitoringPlan,
    PostMarketObservation,
)
from core.compliance.post_market_service import (
    InMemoryPostMarketStore,
    PostMarketReviewScheduler,
    PostMarketService,
    PostMarketStore,
    get_post_market_service,
    reset_post_market_service,
)
from core.compliance.profile import (
    ComplianceProfile,
    ComplianceProfileError,
    ProfileReport,
    enforce_profile,
    evaluate_profile,
)
from core.compliance.prohibited import (
    ProhibitedPractice,
    ProhibitedPracticeError,
    ProhibitionScreening,
    enforce_practices,
    screen_practices,
)
from core.compliance.registry import (
    AiSystemNotFoundError,
    AiSystemRegistry,
    AiSystemStore,
    InMemoryAiSystemStore,
    get_ai_system_registry,
    reset_ai_system_registry,
)
from core.compliance.risk_management import (
    HarmCategory,
    IdentifiedRisk,
    RiskLikelihood,
    RiskManagementSystem,
    RiskSeverity,
    RiskTreatment,
)
from core.compliance.ropa import (
    InternationalTransfer,
    LawfulBasis,
    ProcessingActivity,
    ProcessingRole,
)
from core.compliance.types import (
    AiSystem,
    AnnexIIIArea,
    Art6Derogation,
    ConformityRecord,
    LifecycleStage,
    OperatorRole,
    RiskCategory,
)

__all__ = [
    # Types
    "AiSystem",
    "AnnexIIIArea",
    "Art6Derogation",
    "ConformityRecord",
    "LifecycleStage",
    "OperatorRole",
    "RiskCategory",
    # Art. 5
    "ProhibitedPractice",
    "ProhibitedPracticeError",
    "ProhibitionScreening",
    "screen_practices",
    "enforce_practices",
    # Art. 6
    "ClassificationResult",
    "classify_system",
    "obligations_for",
    # Registry
    "AiSystemRegistry",
    "AiSystemStore",
    "InMemoryAiSystemStore",
    "AiSystemNotFoundError",
    "get_ai_system_registry",
    "reset_ai_system_registry",
    # Art. 11 / Annex IV
    "AnnexIVSection",
    "TechnicalDocumentation",
    "draft_from_system",
    "TechnicalDocumentationService",
    "get_technical_documentation_service",
    # Art. 9
    "HarmCategory",
    "IdentifiedRisk",
    "RiskLikelihood",
    "RiskManagementSystem",
    "RiskSeverity",
    "RiskTreatment",
    "RiskManagementService",
    "get_risk_management_service",
    # Art. 13
    "InstructionsForUse",
    "draft_instructions",
    "InstructionsService",
    "get_instructions_service",
    # Art. 27
    "FriaRisk",
    "FundamentalRightsImpactAssessment",
    "FriaService",
    "get_fria_service",
    # GDPR Art. 35/36
    "DataProtectionImpactAssessment",
    "DpiaRisk",
    "DpiaTrigger",
    "DpiaService",
    "get_dpia_service",
    # GDPR Art. 30
    "InternationalTransfer",
    "LawfulBasis",
    "ProcessingActivity",
    "ProcessingRole",
    "RopaService",
    "get_ropa_service",
    # Art. 72
    "MonitoringMetric",
    "PostMarketMonitoringPlan",
    "PostMarketObservation",
    "PostMarketService",
    "PostMarketStore",
    "InMemoryPostMarketStore",
    "PostMarketReviewScheduler",
    "get_post_market_service",
    "reset_post_market_service",
    # Deployment posture
    "ComplianceProfile",
    "ComplianceProfileError",
    "ProfileReport",
    "evaluate_profile",
    "enforce_profile",
    # Lifecycle
    "reset_document_services",
    "reset_artefact_services",
]
