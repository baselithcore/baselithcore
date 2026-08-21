"""Tests for Art. 9 risk management, Art. 13 instructions and the GDPR DPIA."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from core.compliance.annex_iv import AnnexIVSection, draft_from_system
from core.compliance.artefact_services import (
    DpiaService,
    InstructionsService,
    RiskManagementService,
)
from core.compliance.dpia import (
    DataProtectionImpactAssessment,
    DpiaRisk,
    DpiaTrigger,
)
from core.compliance.instructions import InstructionsForUse, draft_instructions
from core.compliance.persistence import (
    SQLiteDpiaStore,
    SQLiteInstructionsStore,
    SQLiteRiskManagementStore,
)
from core.compliance.post_market import MonitoringMetric, PostMarketMonitoringPlan
from core.compliance.risk_management import (
    HarmCategory,
    IdentifiedRisk,
    RiskManagementSystem,
    RiskTreatment,
)
from core.compliance.types import AiSystem


def _closed_risk(description: str = "under-detection") -> IdentifiedRisk:
    return IdentifiedRisk(
        description=description,
        harm_categories=[HarmCategory.FUNDAMENTAL_RIGHTS],
        under_foreseeable_misuse=True,
        treatment=RiskTreatment.REDUCED,
        measures="Threshold raised; every negative outcome reviewed.",
        residual_severity=None,
        residual_likelihood=None,
    )


def _fully_closed_risk() -> IdentifiedRisk:
    risk = _closed_risk()
    from core.compliance.risk_management import RiskLikelihood, RiskSeverity

    risk.residual_severity = RiskSeverity.MINOR
    risk.residual_likelihood = RiskLikelihood.UNLIKELY
    risk.residual_accepted = True
    risk.accepted_by = "head-of-risk"
    risk.verification = "Shadow run over 4 weeks; no regression."
    return risk


def _complete_risk_file() -> RiskManagementSystem:
    return RiskManagementSystem(
        system_id="sys-1",
        process_description="Quarterly cycle owned by the risk board.",
        intended_purpose="Rank applications for a recruiter shortlist.",
        foreseeable_misuse="Used as the sole decision rather than a shortlist.",
        risks=[_fully_closed_risk()],
        deployer_information="Onboarding deck plus written limitations.",
        testing_regime="Shadow deployment with acceptance thresholds.",
        responsible_contacts=["risk@example.test"],
        post_market_plan_id="plan-1",
    )


class TestRiskEntry:
    def test_an_untreated_risk_names_its_gaps(self):
        risk = IdentifiedRisk(description="drift")
        gaps = risk.gaps()
        assert any("Art. 9(2)(a)" in g for g in gaps)
        assert any("Art. 9(2)(d)" in g for g in gaps)
        assert risk.is_treated is False
        assert risk.is_closed is False

    def test_acceptance_without_verification_does_not_close_a_risk(self):
        risk = _closed_risk()
        from core.compliance.risk_management import RiskLikelihood, RiskSeverity

        risk.residual_severity = RiskSeverity.MINOR
        risk.residual_likelihood = RiskLikelihood.RARE
        risk.residual_accepted = True
        risk.accepted_by = "someone"
        # Art. 9(8): the measures must be tested, not merely declared.
        assert risk.is_treated is True
        assert risk.is_closed is False
        assert any("Art. 9(8)" in g for g in risk.gaps())

    def test_a_fully_treated_risk_closes(self):
        assert _fully_closed_risk().is_closed is True

    def test_acceptance_needs_a_named_accepter(self):
        risk = _fully_closed_risk()
        risk.accepted_by = None
        assert any("who accepted" in g for g in risk.gaps())


class TestRiskFile:
    def test_a_complete_file_reports_complete(self):
        assert _complete_risk_file().is_complete is True

    def test_an_empty_file_names_every_missing_element(self):
        missing = RiskManagementSystem(system_id="s").missing_elements()
        assert any("Art. 9(1)" in m for m in missing)
        assert any("Art. 9(2)(b)" in m for m in missing)
        assert any("Art. 9(2)(c)" in m for m in missing)

    def test_declaring_misuse_without_analysing_it_is_flagged(self):
        file = _complete_risk_file()
        file.risks[0].under_foreseeable_misuse = False
        assert any(
            "no risk was analysed under foreseeable misuse" in m
            for m in file.missing_elements()
        )

    def test_a_never_reviewed_file_becomes_overdue(self):
        file = _complete_risk_file()
        assert file.is_review_overdue() is False
        file.created_at = datetime.now(UTC) - timedelta(days=400)
        assert file.is_review_overdue() is True

    def test_markdown_renders_every_risk(self):
        rendered = _complete_risk_file().to_markdown()
        assert "under-detection" in rendered
        assert "Reasonably foreseeable misuse" in rendered

    def test_round_trips_through_its_dict_payload(self):
        file = _complete_risk_file()
        assert (
            RiskManagementSystem.from_dict(file.to_dict()).to_dict() == file.to_dict()
        )


class TestRiskService:
    async def test_review_is_refused_while_risks_are_open(self):
        service = RiskManagementService()
        file = await service.save(
            RiskManagementSystem(system_id="s", risks=[_closed_risk()])
        )
        with pytest.raises(ValueError, match="risks remain open"):
            await service.review(file.id)

    async def test_review_succeeds_once_every_risk_is_closed(self):
        service = RiskManagementService()
        file = await service.save(_complete_risk_file())
        reviewed = await service.review(file.id)
        assert reviewed.last_reviewed_at is not None

    async def test_overdue_files_are_surfaced(self):
        service = RiskManagementService()
        file = _complete_risk_file()
        file.created_at = datetime.now(UTC) - timedelta(days=400)
        await service.save(file)
        assert [f.id for f in await service.overdue_reviews()] == [file.id]

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        store = SQLiteRiskManagementStore(tmp_path / "risk.db")
        service = RiskManagementService(store=store)
        file = await service.save(_complete_risk_file())
        store.close()

        reopened = SQLiteRiskManagementStore(tmp_path / "risk.db")
        try:
            restored = await reopened.get(file.id)
            assert restored is not None
            assert restored.is_complete is True
        finally:
            reopened.close()


class TestInstructions:
    def test_empty_instructions_name_every_element(self):
        missing = InstructionsForUse(system_id="s", system_name="n").missing_elements()
        assert any("Art. 13(3)(a)" in m for m in missing)
        assert any("Art. 13(3)(f)" in m for m in missing)
        assert len(missing) == 16

    def test_draft_pulls_from_the_records_on_file(self, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "180")
        from core.config.audit import reset_audit_config

        reset_audit_config()
        try:
            system = AiSystem(
                name="cv-screener",
                intended_purpose="Rank applications",
                provider_name="Acme",
                human_oversight_contacts=["talent-lead@acme.test"],
            )
            plan = PostMarketMonitoringPlan(
                system_id=system.id,
                metrics=[MonitoringMetric(name="accuracy", threshold=0.9)],
            )
            instructions = draft_instructions(
                system, risk_file=_complete_risk_file(), monitoring_plan=plan
            )
            assert instructions.intended_purpose == "Rank applications"
            assert "talent-lead@acme.test" in instructions.human_oversight_measures
            assert "under-detection" in instructions.risk_circumstances
            assert "accuracy" in instructions.performance_metrics
            assert "Art. 26(6)" in instructions.log_collection
        finally:
            reset_audit_config()

    def test_a_draft_is_not_complete(self):
        instructions = draft_instructions(AiSystem(name="s"))
        assert instructions.is_complete is False
        assert any("(b)(vii)" in m for m in instructions.missing_elements())

    def test_markdown_marks_undocumented_sections(self):
        rendered = InstructionsForUse(system_id="s", system_name="n").to_markdown()
        assert "**Not documented.**" in rendered
        assert "Art. 26(2)" in rendered

    def test_round_trips_through_its_dict_payload(self):
        instructions = draft_instructions(AiSystem(name="s"))
        restored = InstructionsForUse.from_dict(instructions.to_dict())
        assert restored.to_dict() == instructions.to_dict()

    async def test_issuing_incomplete_instructions_is_refused(self):
        service = InstructionsService()
        record = await service.save(draft_instructions(AiSystem(name="s")))
        with pytest.raises(ValueError, match="Art. 13"):
            await service.issue(record.id)

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        store = SQLiteInstructionsStore(tmp_path / "instructions.db")
        service = InstructionsService(store=store)
        record = await service.save(draft_instructions(AiSystem(name="s")))
        store.close()

        reopened = SQLiteInstructionsStore(tmp_path / "instructions.db")
        try:
            assert await reopened.get(record.id) is not None
        finally:
            reopened.close()


def _complete_dpia(residual_high: bool = False) -> DataProtectionImpactAssessment:
    return DataProtectionImpactAssessment(
        name="applicant screening",
        controller="Acme",
        triggers=[DpiaTrigger.AUTOMATED_EVALUATION],
        processing_description="Ranking applications against role criteria.",
        purposes="Shortlisting for human review.",
        necessity_assessment="No less intrusive means achieves the purpose.",
        proportionality_assessment="Scope limited to submitted application data.",
        risks=[
            DpiaRisk(
                description="Unfair exclusion",
                measures="Human review of every rejection.",
                residual_high_risk=residual_high,
            )
        ],
        safeguards="Human review, contest channel, retention limit.",
        security_measures="Encryption at rest, RBAC, audit logging.",
        dpo_advice="Proceed with the stated safeguards.",
        data_subject_views="Surveyed a candidate panel in March.",
    )


class TestDpia:
    def test_an_empty_assessment_names_every_element(self):
        missing = DataProtectionImpactAssessment(name="x").missing_elements()
        assert any("Art. 35(7)(a)" in m for m in missing)
        assert any("Art. 35(7)(d)" in m for m in missing)
        assert any("Art. 35(2)" in m for m in missing)

    def test_a_complete_assessment_reports_complete(self):
        assert _complete_dpia().is_complete is True

    def test_a_risk_without_measures_is_flagged(self):
        dpia = _complete_dpia()
        dpia.risks[0].measures = ""
        assert any("measures for risk" in m for m in dpia.missing_elements())

    def test_stating_why_views_were_not_sought_satisfies_35_9(self):
        dpia = _complete_dpia()
        dpia.data_subject_views = ""
        dpia.data_subject_views_not_sought_reason = "Disproportionate effort."
        assert dpia.is_complete is True

    def test_high_residual_risk_demands_prior_consultation(self):
        dpia = _complete_dpia(residual_high=True)
        assert dpia.has_residual_high_risk is True
        assert dpia.requires_prior_consultation is True
        assert dpia.may_start_processing is False

    def test_round_trips_through_its_dict_payload(self):
        dpia = _complete_dpia(residual_high=True)
        restored = DataProtectionImpactAssessment.from_dict(dpia.to_dict())
        assert restored.to_dict() == dpia.to_dict()


class TestDpiaService:
    async def test_completing_a_partial_assessment_is_refused(self):
        service = DpiaService()
        dpia = await service.save(DataProtectionImpactAssessment(name="x"))
        with pytest.raises(ValueError, match="Art. 35"):
            await service.complete(dpia.id)

    async def test_completing_unlocks_processing_when_risk_is_low(self):
        service = DpiaService()
        dpia = await service.save(_complete_dpia())
        completed = await service.complete(dpia.id)
        assert completed.may_start_processing is True

    async def test_high_residual_risk_blocks_until_prior_consultation(self):
        service = DpiaService()
        dpia = await service.save(_complete_dpia(residual_high=True))
        # Patch the module logger instead of capturing stdout: the global
        # structlog sink is process-wide mutable state, so a stdout assert
        # is order-dependent under random test ordering.
        with patch("core.compliance.artefact_services.logger") as log:
            completed = await service.complete(dpia.id)
        assert completed.may_start_processing is False
        logged = " ".join(str(c) for c in log.warning.call_args_list)
        assert "Art. 36(1) prior consultation is required" in logged

        consulted = await service.record_prior_consultation(dpia.id)
        assert consulted.may_start_processing is True

    async def test_blocked_lists_what_cannot_start(self):
        service = DpiaService()
        await service.save(_complete_dpia(residual_high=True))
        assert len(await service.blocked()) == 1

    async def test_sqlite_store_survives_a_reopen(self, tmp_path):
        store = SQLiteDpiaStore(tmp_path / "dpia.db")
        service = DpiaService(store=store)
        dpia = await service.save(_complete_dpia())
        store.close()

        reopened = SQLiteDpiaStore(tmp_path / "dpia.db")
        try:
            assert await reopened.get(dpia.id) is not None
        finally:
            reopened.close()


class TestAnnexIVIntegration:
    def test_the_risk_file_fills_annex_iv_section_5(self):
        doc = draft_from_system(AiSystem(name="s"), risk_file=_complete_risk_file())
        assert AnnexIVSection.RISK_MANAGEMENT not in doc.missing_sections()
        assert "under-detection" in doc.sections[AnnexIVSection.RISK_MANAGEMENT]

    def test_instructions_fill_the_monitoring_and_metrics_sections(self):
        instructions = InstructionsForUse(
            system_id="s",
            system_name="n",
            human_oversight_measures="Reviewer signs off every rejection.",
            output_interpretation="Scores are ranks, not probabilities.",
            performance_metrics="accuracy >= 0.9",
        )
        doc = draft_from_system(AiSystem(name="s"), instructions=instructions)
        assert AnnexIVSection.PERFORMANCE_METRICS not in doc.missing_sections()
        assert (
            "Reviewer signs off" in doc.sections[AnnexIVSection.MONITORING_AND_CONTROL]
        )

    def test_without_the_artefacts_those_sections_stay_empty(self):
        doc = draft_from_system(AiSystem(name="s"))
        assert AnnexIVSection.RISK_MANAGEMENT in doc.missing_sections()
        assert AnnexIVSection.PERFORMANCE_METRICS in doc.missing_sections()
