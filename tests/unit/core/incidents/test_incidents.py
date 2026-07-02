"""Tests for the NIS2 Art. 23 incident-reporting subsystem (core/incidents)."""

from datetime import UTC, datetime, timedelta

import pytest

from core.config.incidents import IncidentReportingConfig
from core.incidents import (
    IncidentNotFoundError,
    IncidentService,
    IncidentSeverity,
    IncidentStatus,
    MilestoneKind,
    SecurityIncident,
)

T0 = datetime(2026, 6, 29, 12, 0, 0, tzinfo=UTC)


def _service() -> IncidentService:
    return IncidentService(config=IncidentReportingConfig())


class TestOpenIncident:
    async def test_open_sets_detected_status(self):
        svc = _service()
        inc = await svc.open_incident("breach", IncidentSeverity.HIGH)
        assert inc.status is IncidentStatus.DETECTED
        assert inc.significant is True
        assert (await svc.get(inc.id)) is inc

    async def test_detected_at_anchors_clock(self):
        svc = _service()
        inc = await svc.open_incident(
            "breach", IncidentSeverity.CRITICAL, detected_at=T0
        )
        assert inc.detected_at == T0

    async def test_metadata_recorded(self):
        svc = _service()
        inc = await svc.open_incident(
            "breach",
            IncidentSeverity.MEDIUM,
            affected_systems=["api", "db"],
            affected_subjects=42,
            details={"vector": "sqli"},
        )
        assert inc.affected_systems == ["api", "db"]
        assert inc.affected_subjects == 42
        assert inc.details["vector"] == "sqli"


class TestMilestoneDeadlines:
    def test_nis2_timeline(self):
        inc = SecurityIncident("x", IncidentSeverity.HIGH, detected_at=T0)
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[MilestoneKind.EARLY_WARNING].due_at == T0 + timedelta(hours=24)
        assert ms[MilestoneKind.NOTIFICATION].due_at == T0 + timedelta(hours=72)
        # Final report is one month after the notification due date.
        assert ms[MilestoneKind.FINAL_REPORT].due_at == T0 + timedelta(
            hours=72
        ) + timedelta(days=30)

    def test_non_significant_has_no_milestones(self):
        inc = SecurityIncident(
            "minor", IncidentSeverity.LOW, detected_at=T0, significant=False
        )
        assert inc.milestones() == []

    def test_final_report_anchors_to_actual_notification(self):
        inc = SecurityIncident("x", IncidentSeverity.HIGH, detected_at=T0)
        inc.notification_at = T0 + timedelta(hours=48)  # filed early
        ms = {m.kind: m for m in inc.milestones()}
        assert ms[MilestoneKind.FINAL_REPORT].due_at == T0 + timedelta(
            hours=48
        ) + timedelta(days=30)

    def test_custom_config_deadlines(self):
        svc = IncidentService(
            config=IncidentReportingConfig(
                INCIDENT_EARLY_WARNING_HOURS=12,
                INCIDENT_NOTIFICATION_HOURS=48,
                INCIDENT_FINAL_REPORT_DAYS=14,
            )
        )
        inc = SecurityIncident("x", IncidentSeverity.HIGH, detected_at=T0)
        ms = {m.kind: m for m in svc.milestones(inc)}
        assert ms[MilestoneKind.EARLY_WARNING].due_at == T0 + timedelta(hours=12)
        assert ms[MilestoneKind.NOTIFICATION].due_at == T0 + timedelta(hours=48)


class TestMilestoneStatus:
    def test_overdue_when_past_and_unsubmitted(self):
        inc = SecurityIncident("x", IncidentSeverity.HIGH, detected_at=T0)
        ew = inc.milestones()[0]
        assert ew.is_overdue(T0 + timedelta(hours=25)) is True
        assert ew.is_overdue(T0 + timedelta(hours=1)) is False

    def test_submitted_never_overdue(self):
        inc = SecurityIncident("x", IncidentSeverity.HIGH, detected_at=T0)
        inc.early_warning_at = T0 + timedelta(hours=20)
        ew = inc.milestones()[0]
        assert ew.is_submitted is True
        assert ew.is_overdue(T0 + timedelta(hours=100)) is False

    def test_seconds_remaining_negative_when_overdue(self):
        inc = SecurityIncident("x", IncidentSeverity.HIGH, detected_at=T0)
        ew = inc.milestones()[0]
        assert ew.seconds_remaining(T0 + timedelta(hours=25)) < 0


class TestAdvanceWorkflow:
    async def test_full_lifecycle(self):
        svc = _service()
        inc = await svc.open_incident("breach", IncidentSeverity.HIGH, detected_at=T0)
        ew = await svc.record_early_warning(
            inc.id, submitted_at=T0 + timedelta(hours=10)
        )
        assert ew.status is IncidentStatus.EARLY_WARNING_SUBMITTED
        notif = await svc.record_notification(
            inc.id, submitted_at=T0 + timedelta(hours=50)
        )
        assert notif.status is IncidentStatus.NOTIFICATION_SUBMITTED
        final = await svc.record_final_report(
            inc.id, submitted_at=T0 + timedelta(days=20)
        )
        assert final.status is IncidentStatus.FINAL_SUBMITTED
        closed = await svc.close_incident(inc.id)
        assert closed.status is IncidentStatus.CLOSED

    async def test_status_never_regresses(self):
        svc = _service()
        inc = await svc.open_incident("breach", IncidentSeverity.HIGH, detected_at=T0)
        await svc.record_notification(inc.id, submitted_at=T0 + timedelta(hours=50))
        # A late early-warning stamp must not drag the status backwards.
        stored = await svc.record_early_warning(
            inc.id, submitted_at=T0 + timedelta(hours=60)
        )
        assert stored.status is IncidentStatus.NOTIFICATION_SUBMITTED
        assert stored.early_warning_at == T0 + timedelta(hours=60)

    async def test_unknown_incident_raises(self):
        svc = _service()
        with pytest.raises(IncidentNotFoundError):
            await svc.record_early_warning("does-not-exist")


class TestOverdueAndListing:
    async def test_overdue_detects_missed_early_warning(self):
        svc = _service()
        await svc.open_incident("breach", IncidentSeverity.HIGH, detected_at=T0)
        overdue = await svc.overdue_milestones(now=T0 + timedelta(hours=30))
        kinds = [m.kind for _, m in overdue]
        assert MilestoneKind.EARLY_WARNING in kinds
        assert MilestoneKind.NOTIFICATION not in kinds

    async def test_submitted_milestone_not_overdue(self):
        svc = _service()
        inc = await svc.open_incident("breach", IncidentSeverity.HIGH, detected_at=T0)
        await svc.record_early_warning(inc.id, submitted_at=T0 + timedelta(hours=20))
        overdue = await svc.overdue_milestones(now=T0 + timedelta(hours=30))
        assert overdue == []

    async def test_closed_incident_excluded_from_overdue(self):
        svc = _service()
        inc = await svc.open_incident("breach", IncidentSeverity.HIGH, detected_at=T0)
        await svc.close_incident(inc.id)
        overdue = await svc.overdue_milestones(now=T0 + timedelta(days=365))
        assert overdue == []

    async def test_non_significant_excluded_from_overdue(self):
        svc = _service()
        await svc.open_incident(
            "minor", IncidentSeverity.LOW, significant=False, detected_at=T0
        )
        overdue = await svc.overdue_milestones(now=T0 + timedelta(days=365))
        assert overdue == []

    async def test_list_open_excludes_closed(self):
        svc = _service()
        a = await svc.open_incident("a", IncidentSeverity.HIGH)
        b = await svc.open_incident("b", IncidentSeverity.LOW)
        await svc.close_incident(b.id)
        open_ids = {i.id for i in await svc.list_open()}
        assert a.id in open_ids
        assert b.id not in open_ids

    async def test_list_filter_by_status(self):
        svc = _service()
        await svc.open_incident("a", IncidentSeverity.HIGH)
        b = await svc.open_incident("b", IncidentSeverity.LOW)
        await svc.close_incident(b.id)
        closed = await svc.list_incidents(status=IncidentStatus.CLOSED)
        assert [i.id for i in closed] == [b.id]


class TestSerialization:
    def test_to_dict_round_trips_fields(self):
        inc = SecurityIncident(
            "breach", IncidentSeverity.CRITICAL, detected_at=T0, affected_subjects=3
        )
        d = inc.to_dict()
        assert d["severity"] == "critical"
        assert d["status"] == "detected"
        assert d["affected_subjects"] == 3
        assert d["detected_at"] == T0.isoformat()
