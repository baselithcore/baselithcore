"""Regulatory incident-reporting subsystem (NIS2, DORA, EU AI Act, GDPR).

Structured recording of incidents with their regulatory reporting milestones
plus overdue-deadline detection. Four regimes coexist, because one event can
trigger several clocks at once — a breach of an AI system holding personal data
can be a NIS2 significant incident, an AI Act serious incident, *and* a GDPR
personal data breach, each toward a different authority on a different horizon:

* **NIS2** (EU 2022/2555) Art. 23 — early warning (24h), notification (72h),
  final report (one month); gated by ``INCIDENT_REPORTING_ENABLED``.
* **DORA** (EU 2022/2554) Art. 19 — major ICT-incident classification then
  initial notification (4h), intermediate report (72h), final report (one
  month); gated by ``DORA_INCIDENT_REPORTING_ENABLED``.
* **EU AI Act** (EU 2024/1689) Art. 73 — serious-incident report to the market
  surveillance authority on a category-dependent clock (2 days for critical
  infrastructure or a widespread infringement, 10 days for a death, 15 days
  otherwise); gated by ``AI_ACT_INCIDENT_REPORTING_ENABLED``.
* **GDPR** (EU 2016/679) Art. 33/34 — supervisory-authority notification within
  72h, communication to data subjects on high risk, and the Art. 33(5) register
  of *every* breach; gated by ``GDPR_BREACH_REPORTING_ENABLED``.

All are opt-in and default-off; domain-agnostic infrastructure, so they live in
the Sacred Core.
"""

from core.incidents.ai_act import (
    AiActIncidentStatus,
    AiActSeriousIncident,
    SeriousIncidentCategory,
)
from core.incidents.ai_act_service import (
    AiActIncidentNotFoundError,
    AiActIncidentService,
    AiActIncidentStore,
    InMemoryAiActIncidentStore,
    get_ai_act_incident_service,
)
from core.incidents.dora import (
    DoraClassification,
    DoraImpactAssessment,
    DoraIncident,
    DoraIncidentStatus,
)
from core.incidents.dora_service import (
    DoraIncidentNotFoundError,
    DoraIncidentService,
    DoraIncidentStore,
    InMemoryDoraIncidentStore,
    get_dora_incident_service,
)
from core.incidents.gdpr import (
    Art34Exemption,
    BreachRiskLevel,
    BreachRole,
    BreachStatus,
    PersonalDataBreach,
)
from core.incidents.gdpr_service import (
    BreachNotFoundError,
    BreachService,
    BreachStore,
    InMemoryBreachStore,
    get_breach_service,
)
from core.incidents.service import (
    IncidentNotFoundError,
    IncidentService,
    IncidentStore,
    InMemoryIncidentStore,
    get_incident_service,
)
from core.incidents.types import (
    AiActMilestoneKind,
    DoraMilestoneKind,
    GdprMilestoneKind,
    IncidentSeverity,
    IncidentStatus,
    MilestoneKind,
    ReportingMilestone,
    SecurityIncident,
)

__all__ = [
    # Shared
    "IncidentSeverity",
    "ReportingMilestone",
    # NIS2
    "IncidentStatus",
    "MilestoneKind",
    "SecurityIncident",
    "IncidentStore",
    "InMemoryIncidentStore",
    "IncidentService",
    "IncidentNotFoundError",
    "get_incident_service",
    # DORA
    "DoraMilestoneKind",
    "DoraIncidentStatus",
    "DoraImpactAssessment",
    "DoraClassification",
    "DoraIncident",
    "DoraIncidentStore",
    "InMemoryDoraIncidentStore",
    "DoraIncidentService",
    "DoraIncidentNotFoundError",
    "get_dora_incident_service",
    # EU AI Act Art. 73
    "AiActMilestoneKind",
    "AiActIncidentStatus",
    "AiActSeriousIncident",
    "SeriousIncidentCategory",
    "AiActIncidentStore",
    "InMemoryAiActIncidentStore",
    "AiActIncidentService",
    "AiActIncidentNotFoundError",
    "get_ai_act_incident_service",
    # GDPR Art. 33/34
    "GdprMilestoneKind",
    "BreachRiskLevel",
    "BreachRole",
    "BreachStatus",
    "Art34Exemption",
    "PersonalDataBreach",
    "BreachStore",
    "InMemoryBreachStore",
    "BreachService",
    "BreachNotFoundError",
    "get_breach_service",
]
