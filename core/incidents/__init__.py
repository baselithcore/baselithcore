"""Regulatory security-incident reporting subsystem (NIS2 Art. 23 + DORA Art. 19).

Structured recording of significant security incidents with their regulatory
reporting milestones plus overdue-deadline detection. Two regimes coexist:

* **NIS2** (EU 2022/2555) — early warning (24h), notification (72h), final
  report (one month); gated by ``INCIDENT_REPORTING_ENABLED``.
* **DORA** (EU 2022/2554) — major ICT-incident classification then initial
  notification (4h), intermediate report (72h), final report (one month); gated
  by ``DORA_INCIDENT_REPORTING_ENABLED``.

Both are opt-in and default-off; domain-agnostic infrastructure, so they live in
the Sacred Core.
"""

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
from core.incidents.service import (
    IncidentNotFoundError,
    IncidentService,
    IncidentStore,
    InMemoryIncidentStore,
    get_incident_service,
)
from core.incidents.types import (
    DoraMilestoneKind,
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
]
