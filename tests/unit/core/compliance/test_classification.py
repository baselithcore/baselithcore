"""Tests for Art. 5 screening and Art. 6 risk classification."""

from __future__ import annotations

import pytest

from core.compliance.classification import classify_system, obligations_for
from core.compliance.prohibited import (
    ProhibitedPractice,
    ProhibitedPracticeError,
    enforce_practices,
    screen_practices,
)
from core.compliance.types import (
    AiSystem,
    AnnexIIIArea,
    Art6Derogation,
    RiskCategory,
)


class TestProhibitedPractices:
    def test_clean_declaration_is_not_prohibited(self):
        screening = screen_practices("assistant", [])
        assert screening.is_prohibited is False

    def test_declared_practice_is_prohibited(self):
        screening = screen_practices(
            "scorer", [ProhibitedPractice.SOCIAL_SCORING]
        )
        assert screening.is_prohibited is True
        assert "Art. 5(1)(c)" in screening.to_dict()["descriptions"][0]

    def test_claimed_exemption_marks_it_reviewable_not_prohibited(self):
        screening = screen_practices(
            "vitals",
            [ProhibitedPractice.EMOTION_INFERENCE_WORK_EDUCATION],
            exemption_rationale="Medical monitoring of patient distress.",
        )
        assert screening.is_prohibited is False
        assert screening.exemption_rationale

    def test_enforce_raises_on_a_banned_practice(self):
        with pytest.raises(ProhibitedPracticeError) as excinfo:
            enforce_practices("scraper", [ProhibitedPractice.UNTARGETED_FACIAL_SCRAPING])
        assert ProhibitedPractice.UNTARGETED_FACIAL_SCRAPING in excinfo.value.practices

    def test_enforce_passes_a_clean_declaration(self):
        assert enforce_practices("assistant").is_prohibited is False


class TestClassification:
    def test_prohibited_practice_wins_over_everything(self):
        system = AiSystem(name="s", annex_iii_areas=[AnnexIIIArea.EMPLOYMENT])
        result = classify_system(
            system, prohibited_practices=[ProhibitedPractice.SOCIAL_SCORING]
        )
        assert result.category is RiskCategory.PROHIBITED

    def test_annex_i_safety_component_is_high_risk(self):
        result = classify_system(AiSystem(name="s", annex_i_product=True))
        assert result.category is RiskCategory.HIGH_RISK
        assert "Annex I" in result.citations

    def test_annex_iii_area_is_high_risk(self):
        system = AiSystem(name="s", annex_iii_areas=[AnnexIIIArea.LAW_ENFORCEMENT])
        result = classify_system(system)
        assert result.category is RiskCategory.HIGH_RISK
        assert result.requires_registration is True

    def test_article_6_3_derogation_lowers_the_category(self):
        system = AiSystem(
            name="s",
            annex_iii_areas=[AnnexIIIArea.EDUCATION],
            art6_derogations=[Art6Derogation.NARROW_PROCEDURAL_TASK],
        )
        result = classify_system(system)
        assert result.category is not RiskCategory.HIGH_RISK
        assert result.derogation_claimed is True
        # Art. 49(2): the derogation removes the duties, not the visibility.
        assert result.requires_registration is True

    def test_profiling_defeats_the_derogation(self):
        system = AiSystem(
            name="s",
            annex_iii_areas=[AnnexIIIArea.EMPLOYMENT],
            art6_derogations=[Art6Derogation.PREPARATORY_TASK],
            performs_profiling=True,
        )
        result = classify_system(system)
        assert result.category is RiskCategory.HIGH_RISK
        assert "last subparagraph" in " ".join(result.citations)

    def test_gpai_model_is_its_own_axis(self):
        result = classify_system(AiSystem(name="m", is_gpai_model=True))
        assert result.category is RiskCategory.GPAI

    def test_gpai_with_systemic_risk(self):
        result = classify_system(
            AiSystem(name="m", is_gpai_model=True, gpai_systemic_risk=True)
        )
        assert result.category is RiskCategory.GPAI_SYSTEMIC_RISK

    def test_chatbot_is_limited_risk(self):
        result = classify_system(AiSystem(name="bot", interacts_with_humans=True))
        assert result.category is RiskCategory.LIMITED_RISK
        assert result.citations == ["Art. 50"]

    def test_everything_else_is_minimal_risk(self):
        result = classify_system(AiSystem(name="batch-tagger"))
        assert result.category is RiskCategory.MINIMAL_RISK


class TestObligations:
    def test_high_risk_lists_the_chapter_iii_duties(self):
        duties = obligations_for(RiskCategory.HIGH_RISK)
        joined = " ".join(duties)
        for article in ("Art. 9", "Art. 11", "Art. 12", "Art. 14", "Art. 73"):
            assert article in joined

    def test_systemic_risk_gpai_includes_the_base_gpai_duties(self):
        base = obligations_for(RiskCategory.GPAI)
        systemic = obligations_for(RiskCategory.GPAI_SYSTEMIC_RISK)
        assert all(duty in systemic for duty in base)
        assert any("Art. 55" in duty for duty in systemic)

    def test_prohibited_has_no_compliance_path(self):
        duties = obligations_for(RiskCategory.PROHIBITED)
        assert len(duties) == 1
        assert "banned" in duties[0]
