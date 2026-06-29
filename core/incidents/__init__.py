"""Security-incident reporting subsystem (NIS2 Art. 23).

Structured recording of significant security incidents with the regulatory
reporting milestones — early warning (24h), incident notification (72h), and
final report (one month) — plus overdue-deadline detection. Opt-in and
default-off (``INCIDENT_REPORTING_ENABLED``); domain-agnostic infrastructure,
so it lives in the Sacred Core.
"""

from core.incidents.service import (
    IncidentNotFoundError,
    IncidentService,
    IncidentStore,
    InMemoryIncidentStore,
    get_incident_service,
)
from core.incidents.types import (
    IncidentSeverity,
    IncidentStatus,
    MilestoneKind,
    ReportingMilestone,
    SecurityIncident,
)

__all__ = [
    "IncidentSeverity",
    "IncidentStatus",
    "MilestoneKind",
    "ReportingMilestone",
    "SecurityIncident",
    "IncidentStore",
    "InMemoryIncidentStore",
    "IncidentService",
    "IncidentNotFoundError",
    "get_incident_service",
]
