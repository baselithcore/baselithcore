"""Tests for the GDPR Art. 33/34 personal-data-breach regime."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.config.incidents import IncidentReportingConfig
from core.incidents.gdpr import (
    AUTHORITY_NOTIFICATION_HOURS,
    Art34Exemption,
    BreachRiskLevel,
    BreachRole,
    BreachStatus,
    PersonalDataBreach,
)
from core.incidents.gdpr_service import BreachNotFoundError, BreachService
from core.incidents.persistence import SQLiteBreachStore
from core.incidents.types import GdprMilestoneKind


@pytest.fixture
def service():
    return BreachService(config=IncidentReportingConfig())


class TestObligationScope:
    def test_no_risk_breach_is_registered_but_not_notifiable(self):
        breach = PersonalDataBreach(title="t", risk_level=BreachRiskLevel.NONE)
        assert breach.requires_authority_notification is False
        assert breach.milestones() == []

    def test_risk_breach_notifies_the_authority_only(self):
        breach = PersonalDataBreach(title="t", risk_level=BreachRiskLevel.RISK)
        kinds = [m.kind for m in breach.milestones()]
        assert kinds == [GdprMilestoneKind.AUTHORITY_NOTIFICATION]

    def test_high_risk_breach_also_communicates_to_subjects(self):
        breach = PersonalDataBreach(title="t", risk_level=BreachRiskLevel.HIGH)
        kinds = [m.kind for m in breach.milestones()]
        assert GdprMilestoneKind.SUBJECT_COMMUNICATION in kinds

    def test_article_34_exemption_drops_the_communication_clock(self):
        breach = PersonalDataBreach(
            title="t",
            risk_level=BreachRiskLevel.HIGH,
            subject_exemption=Art34Exemption.PROTECTION_MEASURES,
        )
        assert breach.requires_subject_communication is False
        kinds = [m.kind for m in breach.milestones()]
        assert kinds == [GdprMilestoneKind.AUTHORITY_NOTIFICATION]

    def test_a_processor_reports_to_its_controller_not_the_authority(self):
        breach = PersonalDataBreach(
            title="t", role=BreachRole.PROCESSOR, risk_level=BreachRiskLevel.HIGH
        )
        assert breach.requires_authority_notification is False
        kinds = [m.kind for m in breach.milestones()]
        assert GdprMilestoneKind.AUTHORITY_NOTIFICATION not in kinds


class TestSeventyTwoHourClock:
    def test_deadline_anchors_to_awareness(self):
        aware = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)
        breach = PersonalDataBreach(title="t", became_aware_at=aware)
        milestone = breach.milestones()[0]
        assert milestone.due_at == aware + timedelta(hours=AUTHORITY_NOTIFICATION_HOURS)

    def test_notification_inside_the_window_is_not_late(self):
        aware = datetime.now(UTC) - timedelta(hours=10)
        breach = PersonalDataBreach(
            title="t", became_aware_at=aware, authority_notified_at=datetime.now(UTC)
        )
        assert breach.is_late is False

    def test_notification_past_the_window_is_late(self):
        aware = datetime.now(UTC) - timedelta(hours=100)
        breach = PersonalDataBreach(
            title="t", became_aware_at=aware, authority_notified_at=datetime.now(UTC)
        )
        assert breach.is_late is True

    def test_unnotified_breach_is_not_yet_late(self):
        breach = PersonalDataBreach(
            title="t", became_aware_at=datetime.now(UTC) - timedelta(hours=100)
        )
        assert breach.is_late is False
        assert breach.milestones()[0].is_overdue() is True


class TestServiceWorkflow:
    async def test_every_breach_enters_the_register(self, service):
        breach = await service.record_breach("t", risk_level=BreachRiskLevel.NONE)
        assert await service.get(breach.id) is not None
        assert len(await service.list_breaches()) == 1

    async def test_status_advances_through_the_flow(self, service):
        breach = await service.record_breach("t", risk_level=BreachRiskLevel.HIGH)
        updated = await service.notify_authority(breach.id)
        assert updated.status is BreachStatus.AUTHORITY_NOTIFIED
        updated = await service.communicate_to_subjects(breach.id)
        assert updated.status is BreachStatus.SUBJECTS_COMMUNICATED
        updated = await service.close_breach(breach.id)
        assert updated.status is BreachStatus.CLOSED

    async def test_late_notification_records_its_reason(self, service):
        breach = await service.record_breach(
            "t", became_aware_at=datetime.now(UTC) - timedelta(hours=100)
        )
        updated = await service.notify_authority(
            breach.id, delay_reason="forensics were inconclusive until day 4"
        )
        assert updated.is_late is True
        assert updated.delay_reason

    async def test_late_notification_without_a_reason_is_flagged(self, service):
        from unittest.mock import patch

        breach = await service.record_breach(
            "t", became_aware_at=datetime.now(UTC) - timedelta(hours=100)
        )
        # Patch the module logger instead of capturing stdout: the global
        # structlog sink is process-wide mutable state, so a stdout assert is
        # order-dependent under random test ordering.
        with patch("core.incidents.gdpr_service.logger") as log:
            updated = await service.notify_authority(breach.id)
        assert updated.is_late is True
        assert updated.delay_reason is None
        # The missing Art. 33(1) justification is surfaced, not swallowed.
        logged = " ".join(str(c) for c in log.warning.call_args_list)
        assert "late notification without a reason" in logged

    async def test_claiming_an_exemption_keeps_it_in_the_register(self, service):
        breach = await service.record_breach("t", risk_level=BreachRiskLevel.HIGH)
        updated = await service.claim_exemption(
            breach.id,
            Art34Exemption.PROTECTION_MEASURES,
            rationale="data was AES-256 encrypted at rest",
        )
        assert updated.subject_exemption is Art34Exemption.PROTECTION_MEASURES
        assert updated.details["art34_exemption_rationale"]
        assert service.milestones(updated) == [
            m
            for m in service.milestones(updated)
            if m.kind is GdprMilestoneKind.AUTHORITY_NOTIFICATION
        ]

    async def test_processor_notifies_its_controller(self, service):
        breach = await service.record_breach("t", role=BreachRole.PROCESSOR)
        updated = await service.notify_controller(breach.id)
        assert updated.controller_notified_at is not None
        assert updated.status is BreachStatus.DETECTED

    async def test_unknown_breach_raises(self, service):
        with pytest.raises(BreachNotFoundError):
            await service.notify_authority("nope")

    async def test_overdue_milestones_surface_missed_deadlines(self, service):
        await service.record_breach(
            "t", became_aware_at=datetime.now(UTC) - timedelta(hours=100)
        )
        overdue = await service.overdue_milestones()
        assert len(overdue) == 1
        assert overdue[0][1].kind is GdprMilestoneKind.AUTHORITY_NOTIFICATION

    async def test_closed_breaches_are_not_reported_overdue(self, service):
        breach = await service.record_breach(
            "t", became_aware_at=datetime.now(UTC) - timedelta(hours=100)
        )
        await service.close_breach(breach.id)
        assert await service.overdue_milestones() == []


class TestPersistence:
    def test_round_trips_through_its_dict_payload(self):
        original = PersonalDataBreach(
            title="t",
            risk_level=BreachRiskLevel.HIGH,
            role=BreachRole.PROCESSOR,
            data_categories=["email", "health"],
            affected_subjects=42,
            subject_exemption=Art34Exemption.DISPROPORTIONATE_EFFORT,
            remedial_action="rotated credentials",
        )
        restored = PersonalDataBreach.from_dict(original.to_dict())
        assert restored.to_dict() == original.to_dict()

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        path = tmp_path / "gdpr.db"
        store = SQLiteBreachStore(path)
        service = BreachService(store=store, config=IncidentReportingConfig())
        breach = await service.record_breach("persisted")
        store.close()

        reopened = SQLiteBreachStore(path)
        try:
            restored = await reopened.get(breach.id)
            assert restored is not None
            assert restored.title == "persisted"
        finally:
            reopened.close()
