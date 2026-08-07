"""Tests for the EU AI Act Art. 73 serious-incident regime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config.incidents import IncidentReportingConfig
from core.incidents.ai_act import (
    DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE,
    DEADLINE_DAYS_DEATH,
    DEADLINE_DAYS_DEFAULT,
    AiActIncidentStatus,
    AiActSeriousIncident,
    SeriousIncidentCategory,
    report_deadline_days,
)
from core.incidents.ai_act_service import (
    AiActIncidentNotFoundError,
    AiActIncidentService,
)
from core.incidents.persistence import SQLiteAiActIncidentStore
from core.incidents.types import AiActMilestoneKind

CATEGORY = SeriousIncidentCategory


@pytest.fixture
def service():
    return AiActIncidentService(config=IncidentReportingConfig())


class TestDeadlineDerivation:
    def test_default_horizon_is_fifteen_days(self):
        assert report_deadline_days([]) == DEADLINE_DAYS_DEFAULT
        assert (
            report_deadline_days([CATEGORY.FUNDAMENTAL_RIGHTS_INFRINGEMENT])
            == DEADLINE_DAYS_DEFAULT
        )

    def test_death_shortens_to_ten_days(self):
        assert report_deadline_days([CATEGORY.DEATH]) == DEADLINE_DAYS_DEATH

    def test_critical_infrastructure_shortens_to_two_days(self):
        assert (
            report_deadline_days([CATEGORY.CRITICAL_INFRASTRUCTURE_DISRUPTION])
            == DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE
        )

    def test_widespread_infringement_shortens_to_two_days(self):
        assert (
            report_deadline_days([], widespread_infringement=True)
            == DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE
        )

    def test_shortest_applicable_horizon_wins(self):
        assert (
            report_deadline_days(
                [CATEGORY.DEATH, CATEGORY.CRITICAL_INFRASTRUCTURE_DISRUPTION]
            )
            == DEADLINE_DAYS_CRITICAL_INFRASTRUCTURE
        )


class TestMilestones:
    def test_report_deadline_anchors_to_awareness(self):
        aware = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        incident = AiActSeriousIncident(
            title="t",
            ai_system_id="sys-1",
            became_aware_at=aware,
            categories=[CATEGORY.DEATH],
        )
        report = incident.milestones()[0]
        assert report.kind is AiActMilestoneKind.SERIOUS_INCIDENT_REPORT
        assert report.due_at == aware + timedelta(days=DEADLINE_DAYS_DEATH)

    def test_non_serious_incident_has_no_reporting_clock(self):
        incident = AiActSeriousIncident(title="t", ai_system_id="s", serious=False)
        assert incident.milestones() == []

    def test_complete_report_anchors_to_the_actual_initial_report(self):
        aware = datetime(2026, 8, 1, tzinfo=UTC)
        filed = datetime(2026, 8, 4, tzinfo=UTC)
        incident = AiActSeriousIncident(
            title="t", ai_system_id="s", became_aware_at=aware, report_at=filed
        )
        complete = incident.milestones(complete_report_days=30)[1]
        assert complete.due_at == filed + timedelta(days=30)

    def test_overdue_report_is_detected(self):
        aware = datetime.now(UTC) - timedelta(days=20)
        incident = AiActSeriousIncident(
            title="t", ai_system_id="s", became_aware_at=aware
        )
        assert incident.milestones()[0].is_overdue() is True


class TestServiceWorkflow:
    async def test_open_records_the_governing_deadline(self, service):
        incident = await service.open_incident(
            "model harmed a patient",
            "sys-1",
            categories=[CATEGORY.SERIOUS_HEALTH_HARM],
        )
        assert incident.status is AiActIncidentStatus.DETECTED
        assert incident.deadline_days == DEADLINE_DAYS_DEFAULT
        assert await service.get(incident.id) is not None

    async def test_status_advances_through_the_article_73_flow(self, service):
        incident = await service.open_incident("t", "sys-1")
        await service.record_causal_link(incident.id)
        updated = await service.record_report(incident.id)
        assert updated.status is AiActIncidentStatus.REPORT_SUBMITTED
        updated = await service.record_complete_report(incident.id)
        assert updated.status is AiActIncidentStatus.COMPLETE_REPORT_SUBMITTED
        updated = await service.close_incident(incident.id)
        assert updated.status is AiActIncidentStatus.CLOSED

    async def test_status_never_moves_backwards(self, service):
        incident = await service.open_incident("t", "sys-1")
        await service.record_report(incident.id)
        updated = await service.record_causal_link(incident.id)
        # The timestamp is stamped, but the status stays at the later stage.
        assert updated.causal_link_at is not None
        assert updated.status is AiActIncidentStatus.REPORT_SUBMITTED

    async def test_follow_up_actions_do_not_change_status(self, service):
        incident = await service.open_incident("t", "sys-1")
        await service.record_report(incident.id)
        updated = await service.record_investigation(incident.id)
        assert updated.investigation_at is not None
        assert updated.status is AiActIncidentStatus.REPORT_SUBMITTED
        updated = await service.record_corrective_action(incident.id)
        assert updated.corrective_action_at is not None

    async def test_unknown_incident_raises(self, service):
        with pytest.raises(AiActIncidentNotFoundError):
            await service.record_report("nope")

    async def test_overdue_milestones_surface_missed_deadlines(self, service):
        await service.open_incident(
            "critical infra outage",
            "sys-1",
            categories=[CATEGORY.CRITICAL_INFRASTRUCTURE_DISRUPTION],
            became_aware_at=datetime.now(UTC) - timedelta(days=5),
        )
        overdue = await service.overdue_milestones()
        kinds = {m.kind for _, m in overdue}
        assert AiActMilestoneKind.SERIOUS_INCIDENT_REPORT in kinds

    async def test_closed_incidents_are_not_reported_overdue(self, service):
        incident = await service.open_incident(
            "t",
            "sys-1",
            categories=[CATEGORY.CRITICAL_INFRASTRUCTURE_DISRUPTION],
            became_aware_at=datetime.now(UTC) - timedelta(days=5),
        )
        await service.close_incident(incident.id)
        assert await service.overdue_milestones() == []

    async def test_list_open_excludes_closed(self, service):
        first = await service.open_incident("a", "sys-1")
        await service.open_incident("b", "sys-1")
        await service.close_incident(first.id)
        assert len(await service.list_open()) == 1
        assert len(await service.list_incidents()) == 2


class TestPersistence:
    def test_round_trips_through_its_dict_payload(self):
        original = AiActSeriousIncident(
            title="t",
            ai_system_id="sys-1",
            categories=[CATEGORY.DEATH, CATEGORY.PROPERTY_OR_ENVIRONMENTAL_HARM],
            widespread_infringement=True,
            member_state="IT",
            deployer="acme",
            details={"note": "x"},
        )
        restored = AiActSeriousIncident.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        path = tmp_path / "ai_act.db"
        store = SQLiteAiActIncidentStore(path)
        service = AiActIncidentService(store=store, config=IncidentReportingConfig())
        incident = await service.open_incident("persisted", "sys-1")
        store.close()

        reopened = SQLiteAiActIncidentStore(path)
        try:
            restored = await reopened.get(incident.id)
            assert restored is not None
            assert restored.title == "persisted"
        finally:
            reopened.close()
