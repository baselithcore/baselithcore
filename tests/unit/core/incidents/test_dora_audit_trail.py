"""DORA incidents leave the same audit evidence as the other three regimes."""

from datetime import UTC, datetime
from typing import Any

import pytest

from baselith.core.config.incidents import IncidentReportingConfig
from baselith.core.incidents import DoraImpactAssessment, DoraIncidentService

T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


@pytest.fixture()
def audit(monkeypatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class _Logger:
        async def log(self, event_type, **kwargs):
            records.append({"event_type": event_type.value, **kwargs})

    monkeypatch.setattr(
        "baselith.core.incidents.dora_service.get_audit_logger", lambda: _Logger()
    )
    return records


async def test_the_whole_dora_lifecycle_is_on_the_audit_trail(audit):
    service = DoraIncidentService(config=IncidentReportingConfig())

    incident = await service.open_incident("Core banking down", detected_at=T0)
    await service.classify(
        incident.id,
        DoraImpactAssessment(
            critical_services_affected=True, clients_affected=True, data_losses=True
        ),
        classified_at=T0,
    )
    await service.record_initial_notification(incident.id, submitted_at=T0)
    await service.record_intermediate_report(incident.id, submitted_at=T0)
    await service.record_final_report(incident.id, submitted_at=T0)
    await service.close_incident(incident.id, closed_at=T0)

    assert [(r["event_type"], r["action"]) for r in audit] == [
        ("incident.open", "open"),
        ("incident.milestone", "classified"),
        ("incident.milestone", "initial_notification"),
        ("incident.milestone", "intermediate_report"),
        ("incident.milestone", "final_report"),
        ("incident.close", "closed"),
    ]
    assert all(r["resource"] == incident.id for r in audit)
    assert all(r["details"]["regime"] == "dora" for r in audit)


async def test_the_classification_record_says_what_was_assessed(audit):
    service = DoraIncidentService(config=IncidentReportingConfig())
    incident = await service.open_incident("Card processor degraded", detected_at=T0)

    await service.classify(
        incident.id,
        DoraImpactAssessment(critical_services_affected=True),
        classified_at=T0,
        major_override=True,
    )

    classified = audit[-1]["details"]
    assert classified["is_major"] is True
    assert classified["major_override"] is True
    assert classified["assessment"]["critical_services_affected"] is True
    assert classified["assessment"]["clients_affected"] is False
    assert classified["at"] == T0.isoformat()
