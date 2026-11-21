"""Reporting clocks cannot be moved, loosened or broken by one bad instant."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from baselith.core.config.incidents import IncidentReportingConfig
from baselith.core.incidents import DoraImpactAssessment, DoraIncidentService
from baselith.core.incidents.service import IncidentService
from baselith.core.incidents.types import IncidentSeverity, require_aware

T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)
NAIVE = datetime(2026, 6, 29, 12, 0, 0)  # noqa: DTZ001 - the case under test


@pytest.fixture
def audit(monkeypatch) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    class _Logger:
        async def log(self, event_type, **kwargs):
            records.append({"event_type": event_type.value, **kwargs})

    monkeypatch.setattr(
        "baselith.core.incidents.dora_service.get_audit_logger", lambda: _Logger()
    )
    return records


def _dora() -> DoraIncidentService:
    return DoraIncidentService(config=IncidentReportingConfig())


def _major() -> DoraImpactAssessment:
    return DoraImpactAssessment(
        critical_services_affected=True, clients_affected=True, data_losses=True
    )


def test_an_aware_instant_passes_and_a_naive_one_does_not():
    assert require_aware(T0, "at") is T0
    with pytest.raises(ValueError, match="detected_at must carry a timezone"):
        require_aware(NAIVE, "detected_at")


async def test_classifying_a_major_incident_again_does_not_restart_the_clock():
    svc = _dora()
    inc = await svc.open_incident("outage", detected_at=T0)
    await svc.classify(inc.id, _major(), classified_at=T0 + timedelta(hours=1))

    # Nine hours on, the 4h notification is overdue. A second classification
    # used to move the deadline to "four hours from now".
    later = T0 + timedelta(hours=10)
    again = await svc.classify(inc.id, _major(), classified_at=later)

    assert again.classification is not None
    assert again.classification.classified_at == T0 + timedelta(hours=1)
    overdue = await svc.overdue_milestones(now=later)
    assert [m.kind.value for _, m in overdue] == ["initial_notification"]


async def test_a_first_major_classification_takes_its_own_instant():
    svc = _dora()
    inc = await svc.open_incident("outage", detected_at=T0)
    await svc.classify(
        inc.id, DoraImpactAssessment(), classified_at=T0 + timedelta(hours=1)
    )

    major = await svc.classify(inc.id, _major(), classified_at=T0 + timedelta(hours=3))

    assert major.classification is not None
    assert major.classification.classified_at == T0 + timedelta(hours=3)


async def test_closing_with_reports_unfiled_is_audited_as_a_failure(audit):
    svc = _dora()
    inc = await svc.open_incident("outage", detected_at=T0)
    await svc.classify(inc.id, _major(), classified_at=T0)
    await svc.record_initial_notification(inc.id, submitted_at=T0 + timedelta(hours=1))

    await svc.close_incident(inc.id, closed_at=T0 + timedelta(days=40))

    close = audit[-1]
    assert close["action"] == "closed" and close["success"] is False
    assert close["details"]["unmet_milestones"] == [
        "intermediate_report",
        "final_report",
    ]


async def test_closing_a_non_major_incident_is_an_ordinary_close(audit):
    svc = _dora()
    inc = await svc.open_incident("blip", detected_at=T0)

    await svc.close_incident(inc.id, closed_at=T0 + timedelta(hours=1))

    assert audit[-1]["success"] is True
    assert "unmet_milestones" not in audit[-1]["details"]


async def test_no_register_stores_a_naive_instant():
    dora = _dora()
    with pytest.raises(ValueError, match="timezone"):
        await dora.open_incident("x", detected_at=NAIVE)
    inc = await dora.open_incident("x", detected_at=T0)
    with pytest.raises(ValueError, match="timezone"):
        await dora.classify(inc.id, _major(), classified_at=NAIVE)
    with pytest.raises(ValueError, match="timezone"):
        await dora.close_incident(inc.id, closed_at=NAIVE)
    # Nothing was written by the refused calls: the sweep still runs.
    assert await dora.overdue_milestones(now=T0) == []

    nis2 = IncidentService(config=IncidentReportingConfig())
    with pytest.raises(ValueError, match="timezone"):
        await nis2.open_incident("x", IncidentSeverity.HIGH, detected_at=NAIVE)
    assert await nis2.overdue_milestones(now=T0) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("INCIDENT_EARLY_WARNING_HOURS", 25),
        ("INCIDENT_NOTIFICATION_HOURS", 73),
        ("DORA_INITIAL_NOTIFICATION_HOURS", 48),
        ("DORA_AWARENESS_CAP_HOURS", 25),
        ("DORA_INTERMEDIATE_REPORT_HOURS", 96),
    ],
)
def test_a_setting_cannot_push_a_statutory_deadline_out(field, value):
    with pytest.raises(ValidationError):
        IncidentReportingConfig(**{field: value})


def test_a_setting_can_bring_a_deadline_forward():
    config = IncidentReportingConfig(
        DORA_INITIAL_NOTIFICATION_HOURS=2, INCIDENT_NOTIFICATION_HOURS=48
    )

    assert config.dora_initial_notification_hours == 2
    assert config.notification_hours == 48
