"""Tests for the DORA Art. 19 major-incident reporting subsystem.

Covers classification (Delegated Regulation (EU) 2024/1772), the 4h/24h-cap /
72h / one-month reporting clock, monotonic status, and overdue detection.
"""

from datetime import UTC, datetime, timedelta

import pytest

from core.config.incidents import IncidentReportingConfig
from core.incidents import (
    DoraClassification,
    DoraImpactAssessment,
    DoraIncident,
    DoraIncidentNotFoundError,
    DoraIncidentService,
    DoraIncidentStatus,
    DoraMilestoneKind,
    IncidentSeverity,
)

T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _service() -> DoraIncidentService:
    return DoraIncidentService(config=IncidentReportingConfig())


def _major() -> DoraImpactAssessment:
    """A criteria set that resolves to major: critical services + two others."""
    return DoraImpactAssessment(
        critical_services_affected=True,
        clients_affected=True,
        data_losses=True,
    )


class TestClassification:
    def test_major_when_critical_plus_two_others(self):
        c = DoraClassification(_major(), classified_at=T0)
        assert c.is_major is True

    def test_not_major_without_critical_services(self):
        a = DoraImpactAssessment(
            clients_affected=True, data_losses=True, economic_impact=True
        )
        assert DoraClassification(a, classified_at=T0).is_major is False

    def test_not_major_with_critical_but_one_other(self):
        a = DoraImpactAssessment(critical_services_affected=True, clients_affected=True)
        assert DoraClassification(a, classified_at=T0).is_major is False

    def test_override_forces_major(self):
        a = DoraImpactAssessment()  # nothing triggered
        c = DoraClassification(a, classified_at=T0, major_override=True)
        assert c.is_major is True

    def test_override_can_force_not_major(self):
        c = DoraClassification(_major(), classified_at=T0, major_override=False)
        assert c.is_major is False

    def test_other_criteria_excludes_critical(self):
        assert _major().other_criteria_met() == 2


class TestMilestoneDeadlines:
    def test_initial_due_is_four_hours_after_classification(self):
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(_major(), classified_at=T0)
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[DoraMilestoneKind.INITIAL_NOTIFICATION].due_at == T0 + timedelta(
            hours=4
        )

    def test_initial_capped_at_24h_from_awareness(self):
        # Classified 22h after awareness: 22h + 4h = 26h would breach the 24h cap,
        # so the binding deadline is the earlier 24h-from-detection moment.
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(
            _major(), classified_at=T0 + timedelta(hours=22)
        )
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[DoraMilestoneKind.INITIAL_NOTIFICATION].due_at == T0 + timedelta(
            hours=24
        )

    def test_intermediate_72h_after_initial_due(self):
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(_major(), classified_at=T0)
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[DoraMilestoneKind.INTERMEDIATE_REPORT].due_at == T0 + timedelta(
            hours=4
        ) + timedelta(hours=72)

    def test_intermediate_anchors_to_actual_initial(self):
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(_major(), classified_at=T0)
        inc.initial_notification_at = T0 + timedelta(hours=2)  # filed early
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[DoraMilestoneKind.INTERMEDIATE_REPORT].due_at == T0 + timedelta(
            hours=2
        ) + timedelta(hours=72)

    def test_final_one_month_after_intermediate(self):
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(_major(), classified_at=T0)
        inc.intermediate_report_at = T0 + timedelta(hours=50)
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[DoraMilestoneKind.FINAL_REPORT].due_at == T0 + timedelta(
            hours=50
        ) + timedelta(days=30)

    def test_unclassified_has_no_milestones(self):
        inc = DoraIncident("x", detected_at=T0)
        assert inc.milestones() == []

    def test_non_major_has_no_milestones(self):
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(
            DoraImpactAssessment(), classified_at=T0
        )
        assert inc.milestones() == []

    def test_custom_config_deadlines(self):
        svc = DoraIncidentService(
            config=IncidentReportingConfig(
                DORA_INITIAL_NOTIFICATION_HOURS=2,
                DORA_INTERMEDIATE_REPORT_HOURS=48,
            )
        )
        inc = DoraIncident("x", detected_at=T0)
        inc.classification = DoraClassification(_major(), classified_at=T0)
        ms = {m.kind: m for m in svc.milestones(inc)}
        assert ms[DoraMilestoneKind.INITIAL_NOTIFICATION].due_at == T0 + timedelta(
            hours=2
        )
        assert ms[DoraMilestoneKind.INTERMEDIATE_REPORT].due_at == T0 + timedelta(
            hours=2
        ) + timedelta(hours=48)


class TestWorkflow:
    async def test_open_is_detected_and_unclassified(self):
        svc = _service()
        inc = await svc.open_incident("outage", IncidentSeverity.CRITICAL)
        assert inc.status is DoraIncidentStatus.DETECTED
        assert inc.is_major is False
        assert (await svc.get(inc.id)) is inc

    async def test_classify_major_advances_status(self):
        svc = _service()
        inc = await svc.open_incident("outage", detected_at=T0)
        classified = await svc.classify(inc.id, _major(), classified_at=T0)
        assert classified.status is DoraIncidentStatus.CLASSIFIED
        assert classified.is_major is True

    async def test_classify_non_major_stays_detected(self):
        svc = _service()
        inc = await svc.open_incident("blip", detected_at=T0)
        classified = await svc.classify(inc.id, DoraImpactAssessment())
        assert classified.status is DoraIncidentStatus.DETECTED
        assert classified.is_major is False

    async def test_full_lifecycle(self):
        svc = _service()
        inc = await svc.open_incident("outage", detected_at=T0)
        await svc.classify(inc.id, _major(), classified_at=T0)
        initial = await svc.record_initial_notification(
            inc.id, submitted_at=T0 + timedelta(hours=3)
        )
        assert initial.status is DoraIncidentStatus.INITIAL_SUBMITTED
        inter = await svc.record_intermediate_report(
            inc.id, submitted_at=T0 + timedelta(hours=60)
        )
        assert inter.status is DoraIncidentStatus.INTERMEDIATE_SUBMITTED
        final = await svc.record_final_report(
            inc.id, submitted_at=T0 + timedelta(days=20)
        )
        assert final.status is DoraIncidentStatus.FINAL_SUBMITTED
        closed = await svc.close_incident(inc.id)
        assert closed.status is DoraIncidentStatus.CLOSED

    async def test_status_never_regresses(self):
        svc = _service()
        inc = await svc.open_incident("outage", detected_at=T0)
        await svc.classify(inc.id, _major(), classified_at=T0)
        await svc.record_intermediate_report(
            inc.id, submitted_at=T0 + timedelta(hours=60)
        )
        stored = await svc.record_initial_notification(
            inc.id, submitted_at=T0 + timedelta(hours=70)
        )
        assert stored.status is DoraIncidentStatus.INTERMEDIATE_SUBMITTED
        assert stored.initial_notification_at == T0 + timedelta(hours=70)

    async def test_unknown_incident_raises(self):
        svc = _service()
        with pytest.raises(DoraIncidentNotFoundError):
            await svc.record_initial_notification("nope")
        with pytest.raises(DoraIncidentNotFoundError):
            await svc.classify("nope", _major())


class TestOverdueAndListing:
    async def test_overdue_detects_missed_initial(self):
        svc = _service()
        inc = await svc.open_incident("outage", detected_at=T0)
        await svc.classify(inc.id, _major(), classified_at=T0)
        overdue = await svc.overdue_milestones(now=T0 + timedelta(hours=5))
        kinds = [m.kind for _, m in overdue]
        assert DoraMilestoneKind.INITIAL_NOTIFICATION in kinds
        assert DoraMilestoneKind.INTERMEDIATE_REPORT not in kinds

    async def test_unclassified_never_overdue(self):
        svc = _service()
        await svc.open_incident("outage", detected_at=T0)
        overdue = await svc.overdue_milestones(now=T0 + timedelta(days=365))
        assert overdue == []

    async def test_submitted_milestone_not_overdue(self):
        svc = _service()
        inc = await svc.open_incident("outage", detected_at=T0)
        await svc.classify(inc.id, _major(), classified_at=T0)
        await svc.record_initial_notification(
            inc.id, submitted_at=T0 + timedelta(hours=2)
        )
        overdue = await svc.overdue_milestones(now=T0 + timedelta(hours=5))
        assert overdue == []

    async def test_closed_excluded_from_overdue(self):
        svc = _service()
        inc = await svc.open_incident("outage", detected_at=T0)
        await svc.classify(inc.id, _major(), classified_at=T0)
        await svc.close_incident(inc.id)
        overdue = await svc.overdue_milestones(now=T0 + timedelta(days=365))
        assert overdue == []

    async def test_list_open_excludes_closed(self):
        svc = _service()
        a = await svc.open_incident("a")
        b = await svc.open_incident("b")
        await svc.close_incident(b.id)
        open_ids = {i.id for i in await svc.list_open()}
        assert a.id in open_ids
        assert b.id not in open_ids

    async def test_list_filter_by_status(self):
        svc = _service()
        await svc.open_incident("a")
        b = await svc.open_incident("b")
        await svc.close_incident(b.id)
        closed = await svc.list_incidents(status=DoraIncidentStatus.CLOSED)
        assert [i.id for i in closed] == [b.id]


class TestSerialization:
    def test_to_dict_round_trips_fields(self):
        inc = DoraIncident(
            "outage",
            IncidentSeverity.CRITICAL,
            detected_at=T0,
            affected_clients=7,
        )
        inc.classification = DoraClassification(_major(), classified_at=T0)
        d = inc.to_dict()
        assert d["severity"] == "critical"
        assert d["status"] == "detected"
        assert d["affected_clients"] == 7
        assert d["is_major"] is True
        assert d["classification"]["is_major"] is True
        assert d["detected_at"] == T0.isoformat()
