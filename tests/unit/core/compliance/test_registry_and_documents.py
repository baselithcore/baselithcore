"""Tests for the AI system registry and the compliance document services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from core.compliance.annex_iv import (
    AnnexIVSection,
    TechnicalDocumentation,
    draft_from_system,
)
from core.compliance.documents import (
    FriaService,
    RopaService,
    TechnicalDocumentationService,
)
from core.compliance.fria import FriaRisk, FundamentalRightsImpactAssessment
from core.compliance.persistence import (
    SQLiteAiSystemStore,
    SQLiteFriaStore,
    SQLiteRopaStore,
    SQLiteTechnicalDocumentationStore,
)
from core.compliance.post_market import (
    MonitoringMetric,
    PostMarketMonitoringPlan,
    ThresholdDirection,
)
from core.compliance.prohibited import ProhibitedPractice
from core.compliance.registry import AiSystemNotFoundError, AiSystemRegistry
from core.compliance.ropa import ProcessingActivity, ProcessingRole
from core.compliance.types import (
    AiSystem,
    AnnexIIIArea,
    ConformityRecord,
    LifecycleStage,
    RiskCategory,
)


@pytest.fixture
def registry():
    return AiSystemRegistry()


class TestRegistry:
    async def test_register_derives_and_stores_the_category(self, registry):
        system, result = await registry.register(
            AiSystem(name="cv-screener", annex_iii_areas=[AnnexIIIArea.EMPLOYMENT])
        )
        assert system.risk_category is RiskCategory.HIGH_RISK
        assert result is not None and result.requires_registration
        assert system.classified_at is not None
        assert await registry.get(system.id) is not None

    async def test_operator_can_pin_the_category(self, registry):
        system, result = await registry.register(
            AiSystem(name="pinned", risk_category=RiskCategory.HIGH_RISK),
            classify=False,
        )
        assert result is None
        assert system.risk_category is RiskCategory.HIGH_RISK
        assert system.classified_at is None

    async def test_prohibited_declaration_is_recorded_not_raised(self, registry):
        system, result = await registry.register(
            AiSystem(name="scorer"),
            prohibited_practices=[ProhibitedPractice.SOCIAL_SCORING],
        )
        assert system.risk_category is RiskCategory.PROHIBITED
        assert result is not None and result.category is RiskCategory.PROHIBITED

    async def test_reclassification_reflects_changed_facts(self, registry):
        system, _ = await registry.register(AiSystem(name="s"))
        assert system.risk_category is RiskCategory.MINIMAL_RISK
        system.annex_iii_areas = [AnnexIIIArea.ESSENTIAL_SERVICES]
        updated, result = await registry.reclassify(system.id)
        assert updated.risk_category is RiskCategory.HIGH_RISK
        assert "Annex III" in " ".join(result.citations)

    async def test_lifecycle_stamps_the_market_date(self, registry):
        system, _ = await registry.register(AiSystem(name="s"))
        updated = await registry.advance_lifecycle(
            system.id, LifecycleStage.PLACED_ON_MARKET
        )
        assert updated.placed_on_market_at is not None
        withdrawn = await registry.advance_lifecycle(
            system.id, LifecycleStage.WITHDRAWN
        )
        assert withdrawn.withdrawn_at is not None

    async def test_pending_eu_registration_is_surfaced(self, registry):
        await registry.register(
            AiSystem(name="hr", annex_iii_areas=[AnnexIIIArea.EMPLOYMENT])
        )
        pending = await registry.unregistered_with_authority()
        assert len(pending) == 1

    async def test_registered_system_drops_off_the_pending_list(self, registry):
        system, _ = await registry.register(
            AiSystem(
                name="hr",
                annex_iii_areas=[AnnexIIIArea.EMPLOYMENT],
                conformity=ConformityRecord(
                    eu_database_registration_at=datetime.now(UTC),
                    eu_database_id="EU-123",
                ),
            )
        )
        assert await registry.unregistered_with_authority() == []
        assert system.requires_registration is True

    async def test_withdrawn_systems_are_not_pending(self, registry):
        system, _ = await registry.register(
            AiSystem(name="hr", annex_iii_areas=[AnnexIIIArea.EMPLOYMENT])
        )
        await registry.advance_lifecycle(system.id, LifecycleStage.WITHDRAWN)
        assert await registry.unregistered_with_authority() == []

    async def test_summary_rolls_up_the_inventory(self, registry):
        await registry.register(AiSystem(name="a", interacts_with_humans=True))
        await registry.register(
            AiSystem(name="b", annex_iii_areas=[AnnexIIIArea.BIOMETRICS])
        )
        summary = await registry.summary()
        assert summary["total"] == 2
        assert summary["by_category"]["high_risk"] == 1
        assert len(summary["pending_eu_registration"]) == 1

    async def test_obligations_follow_the_category(self, registry):
        system, _ = await registry.register(
            AiSystem(name="s", annex_iii_areas=[AnnexIIIArea.MIGRATION])
        )
        assert any("Art. 11" in o for o in await registry.obligations(system.id))

    async def test_unknown_system_raises(self, registry):
        with pytest.raises(AiSystemNotFoundError):
            await registry.require("nope")

    async def test_round_trips_through_sqlite(self, tmp_path):
        store = SQLiteAiSystemStore(tmp_path / "systems.db")
        reg = AiSystemRegistry(store=store)
        system, _ = await reg.register(
            AiSystem(name="persisted", annex_iii_areas=[AnnexIIIArea.EDUCATION])
        )
        store.close()

        reopened = SQLiteAiSystemStore(tmp_path / "systems.db")
        try:
            restored = await reopened.get(system.id)
            assert restored is not None
            assert restored.name == "persisted"
            assert restored.risk_category is RiskCategory.HIGH_RISK
            assert await reopened.delete(system.id) is True
        finally:
            reopened.close()


class TestAnnexIVDocumentation:
    def test_draft_prefills_what_the_registry_knows(self):
        system = AiSystem(
            name="triage",
            version="2.1.0",
            intended_purpose="Rank incoming cases",
            provider_name="Acme",
            models=["claude-opus-4-8"],
            human_oversight_contacts=["clinical-lead@acme.example"],
        )
        doc = draft_from_system(system)
        general = doc.sections[AnnexIVSection.GENERAL_DESCRIPTION]
        assert "Rank incoming cases" in general
        assert "claude-opus-4-8" in general
        assert "clinical-lead@acme.example" in (
            doc.sections[AnnexIVSection.MONITORING_AND_CONTROL]
        )

    def test_draft_is_not_complete(self):
        doc = draft_from_system(AiSystem(name="s"))
        missing = doc.missing_sections()
        assert AnnexIVSection.DEVELOPMENT_PROCESS in missing
        assert AnnexIVSection.RISK_MANAGEMENT in missing
        assert doc.is_complete is False

    def test_filling_every_section_completes_it(self):
        doc = TechnicalDocumentation(system_id="s1", system_name="s")
        for section in AnnexIVSection:
            doc.set_section(section, "documented")
        assert doc.is_complete is True

    def test_markdown_marks_undocumented_sections(self):
        doc = TechnicalDocumentation(system_id="s1", system_name="s")
        rendered = doc.to_markdown()
        assert "**Not documented.**" in rendered
        assert "Annex IV" in rendered

    def test_round_trips_through_its_dict_payload(self):
        doc = draft_from_system(AiSystem(name="s"))
        assert TechnicalDocumentation.from_dict(doc.to_dict()).to_dict() == doc.to_dict()

    async def test_service_flags_incomplete_documents(self):
        service = TechnicalDocumentationService()
        doc = await service.save(draft_from_system(AiSystem(name="s")))
        assert doc in await service.incomplete()
        assert await service.for_system(doc.system_id) == [doc]

    async def test_approving_an_incomplete_document_is_flagged(self, capsys):
        service = TechnicalDocumentationService()
        doc = await service.save(draft_from_system(AiSystem(name="s")))
        approved = await service.approve(doc.id, "quality-lead")
        assert approved.approved_by == "quality-lead"
        assert "approved while incomplete" in capsys.readouterr().out

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        store = SQLiteTechnicalDocumentationStore(tmp_path / "docs.db")
        service = TechnicalDocumentationService(store=store)
        doc = await service.save(draft_from_system(AiSystem(name="s")))
        store.close()

        reopened = SQLiteTechnicalDocumentationStore(tmp_path / "docs.db")
        try:
            assert await reopened.get(doc.id) is not None
        finally:
            reopened.close()


class TestFria:
    def _complete(self) -> FundamentalRightsImpactAssessment:
        return FundamentalRightsImpactAssessment(
            system_id="s1",
            deployer="City of Example",
            processes_description="Benefit eligibility pre-screening.",
            usage_period="12 months from go-live",
            usage_frequency="Per application, ~400/day",
            affected_categories=["applicants", "dependants"],
            risks=[FriaRisk(description="Under-detection for atypical households")],
            human_oversight_measures="Caseworker reviews every negative outcome.",
            measures_if_materialised="Suspend automation, re-review manually.",
            governance_arrangements="Monthly review by the data ethics board.",
            complaint_mechanism="Published appeal route with a 10-day SLA.",
        )

    def test_empty_assessment_names_every_missing_element(self):
        fria = FundamentalRightsImpactAssessment(system_id="s1", deployer="d")
        missing = fria.missing_elements()
        assert any("Art. 27(1)(a)" in m for m in missing)
        assert any("Art. 27(1)(f)" in m for m in missing)
        assert fria.is_complete is False

    def test_a_filled_assessment_is_complete(self):
        assert self._complete().is_complete is True

    async def test_service_refuses_to_complete_a_partial_assessment(self):
        service = FriaService()
        fria = await service.save(
            FundamentalRightsImpactAssessment(system_id="s1", deployer="d")
        )
        with pytest.raises(ValueError, match="Art. 27"):
            await service.complete(fria.id)

    async def test_service_completes_a_filled_assessment(self):
        service = FriaService()
        fria = await service.save(self._complete())
        completed = await service.complete(fria.id)
        assert completed.completed_at is not None
        notified = await service.notify_authority(fria.id)
        assert notified.authority_notified_at is not None

    def test_round_trips_through_its_dict_payload(self):
        fria = self._complete()
        restored = FundamentalRightsImpactAssessment.from_dict(fria.to_dict())
        assert restored.to_dict() == fria.to_dict()

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        store = SQLiteFriaStore(tmp_path / "fria.db")
        service = FriaService(store=store)
        fria = await service.save(self._complete())
        store.close()

        reopened = SQLiteFriaStore(tmp_path / "fria.db")
        try:
            assert await reopened.get(fria.id) is not None
        finally:
            reopened.close()


class TestRopa:
    def _complete(self) -> ProcessingActivity:
        return ProcessingActivity(
            name="Support ticket triage",
            controller_name="Acme",
            controller_contact="dpo@acme.example",
            purposes=["Route tickets to the right queue"],
            data_subject_categories=["customers"],
            personal_data_categories=["email", "ticket body"],
            recipient_categories=["support staff"],
            retention_period="24 months after ticket closure",
            security_measures="Encryption at rest, RBAC, audit logging.",
        )

    def test_empty_entry_names_every_missing_element(self):
        activity = ProcessingActivity(name="x")
        missing = activity.missing_elements()
        assert any("Art. 30(1)(a)" in m for m in missing)
        assert any("Art. 30(1)(g)" in m for m in missing)

    def test_a_filled_entry_is_complete(self):
        assert self._complete().is_complete is True

    def test_a_transfer_without_safeguards_is_incomplete(self):
        from core.compliance.ropa import InternationalTransfer

        activity = self._complete()
        activity.transfers = [InternationalTransfer(destination="US")]
        assert any("Art. 30(1)(e)" in m for m in activity.missing_elements())

    def test_processor_entries_have_a_reduced_requirement_set(self):
        activity = ProcessingActivity(
            name="hosting",
            role=ProcessingRole.PROCESSOR,
            controller_name="Acme",
            controller_contact="dpo@acme.example",
            recipient_categories=["sub-processor: CDN"],
            security_measures="Encryption in transit and at rest.",
        )
        assert activity.is_complete is True

    async def test_service_tracks_reviews_and_gaps(self):
        service = RopaService()
        activity = await service.save(ProcessingActivity(name="incomplete"))
        assert activity in await service.incomplete()
        reviewed = await service.review(activity.id)
        assert reviewed.reviewed_at is not None

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        store = SQLiteRopaStore(tmp_path / "ropa.db")
        service = RopaService(store=store)
        activity = await service.save(self._complete())
        store.close()

        reopened = SQLiteRopaStore(tmp_path / "ropa.db")
        try:
            assert await reopened.get(activity.id) is not None
        finally:
            reopened.close()


class TestPostMarketMonitoring:
    def _plan(self) -> PostMarketMonitoringPlan:
        return PostMarketMonitoringPlan(
            system_id="s1",
            objectives="Detect accuracy drift and rising escalation rates.",
            metrics=[
                MonitoringMetric(
                    name="accuracy",
                    threshold=0.9,
                    direction=ThresholdDirection.LOWER_BOUND,
                ),
                MonitoringMetric(
                    name="escalation_rate",
                    threshold=0.15,
                    direction=ThresholdDirection.UPPER_BOUND,
                ),
                MonitoringMetric(name="volume"),  # observed, not alerting
            ],
            data_sources=["inference logs", "human review queue"],
            corrective_action_process="Freeze rollout, open an Art. 73 assessment.",
            responsible_contacts=["ml-ops@acme.example"],
        )

    def test_lower_bound_breach(self):
        plan = self._plan()
        assert plan.observe("accuracy", 0.85).is_breach is True
        assert plan.observe("accuracy", 0.95).is_breach is False

    def test_upper_bound_breach(self):
        plan = self._plan()
        assert plan.observe("escalation_rate", 0.2).is_breach is True
        assert plan.observe("escalation_rate", 0.1).is_breach is False

    def test_metric_without_a_threshold_never_breaches(self):
        plan = self._plan()
        assert plan.observe("volume", 10_000_000).is_breach is False

    def test_undeclared_metric_raises(self):
        with pytest.raises(KeyError):
            self._plan().observe("unknown", 1.0)

    def test_breaches_can_be_filtered_by_time(self):
        plan = self._plan()
        plan.observe("accuracy", 0.5, at=datetime.now(UTC) - timedelta(days=10))
        plan.observe("accuracy", 0.5)
        recent = plan.breaches(since=datetime.now(UTC) - timedelta(days=1))
        assert len(plan.breaches()) == 2
        assert len(recent) == 1

    def test_a_never_reviewed_plan_becomes_overdue(self):
        plan = self._plan()
        assert plan.is_review_overdue() is False
        plan.created_at = datetime.now(UTC) - timedelta(days=200)
        assert plan.is_review_overdue() is True

    def test_review_resets_the_cadence(self):
        plan = self._plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=200)
        plan.last_reviewed_at = datetime.now(UTC)
        assert plan.is_review_overdue() is False
        assert plan.review_due_at() is not None

    def test_completeness_names_missing_plan_elements(self):
        empty = PostMarketMonitoringPlan(system_id="s1")
        assert empty.is_complete is False
        assert any("Art. 72" in m for m in empty.missing_elements())
        assert self._plan().is_complete is True

    def test_round_trips_through_its_dict_payload(self):
        plan = self._plan()
        plan.observe("accuracy", 0.5)
        assert PostMarketMonitoringPlan.from_dict(plan.to_dict()).to_dict() == (
            plan.to_dict()
        )
