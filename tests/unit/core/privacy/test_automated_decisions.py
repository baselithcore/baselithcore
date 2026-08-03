"""Tests for the GDPR Art. 22 automated decision-making register."""

from __future__ import annotations

from core.privacy.automated_decisions import (
    Art22Ground,
    AutomatedDecisionActivity,
    AutomatedDecisionRegistry,
)


def _compliant() -> AutomatedDecisionActivity:
    return AutomatedDecisionActivity(
        name="credit pre-screening",
        description="Scores applications before a human underwriter sees them.",
        ground=Art22Ground.CONTRACT,
        human_intervention_channel="Reply to the decision email; an underwriter reviews.",
        express_view_channel="Free-text field on the appeal form.",
        contest_channel="Appeal form, 10 working-day SLA.",
        logic_explanation="Weighted score over income, obligations and history.",
        significance_and_consequences="A negative score delays the application "
        "pending manual review; it never rejects on its own.",
    )


class TestScope:
    def test_both_conditions_are_needed_for_article_22(self):
        activity = AutomatedDecisionActivity(name="x", legal_or_significant_effect=False)
        assert activity.in_scope is False
        activity = AutomatedDecisionActivity(name="x", solely_automated=False)
        assert activity.in_scope is False
        assert AutomatedDecisionActivity(name="x").in_scope is True

    def test_out_of_scope_activities_only_need_a_description(self):
        activity = AutomatedDecisionActivity(
            name="x", solely_automated=False, description="human decides"
        )
        assert activity.missing_elements() == []
        assert activity.is_compliant is True


class TestSafeguards:
    def test_no_ground_is_reported_as_prohibited(self):
        missing = AutomatedDecisionActivity(name="x").missing_elements()
        assert any("Art. 22(2)" in m and "prohibited" in m for m in missing)

    def test_contract_ground_requires_the_three_safeguards(self):
        activity = AutomatedDecisionActivity(name="x", ground=Art22Ground.CONTRACT)
        assert activity.requires_art22_3_safeguards is True
        missing = activity.missing_elements()
        assert any("human intervention" in m for m in missing)
        assert any("express a point of view" in m for m in missing)
        assert any("contest the decision" in m for m in missing)

    def test_consent_ground_requires_them_too(self):
        activity = AutomatedDecisionActivity(
            name="x", ground=Art22Ground.EXPLICIT_CONSENT
        )
        assert activity.requires_art22_3_safeguards is True

    def test_legal_authorisation_defers_to_that_law(self):
        activity = AutomatedDecisionActivity(
            name="x",
            ground=Art22Ground.LEGAL_AUTHORISATION,
            logic_explanation="statutory formula",
            significance_and_consequences="benefit amount",
        )
        # The safeguards come from the authorising law, which this module
        # cannot verify — so it does not demand its own three channels.
        assert activity.requires_art22_3_safeguards is False
        assert activity.is_compliant is True

    def test_special_categories_need_their_own_ground(self):
        activity = _compliant()
        activity.uses_special_categories = True
        assert any("Art. 22(4)" in m for m in activity.missing_elements())
        activity.special_category_ground = "Art. 9(2)(a) explicit consent"
        assert activity.is_compliant is True

    def test_a_fully_declared_activity_is_compliant(self):
        assert _compliant().is_compliant is True


class TestTransparency:
    def test_missing_logic_explanation_is_reported(self):
        activity = _compliant()
        activity.logic_explanation = ""
        assert any("logic involved" in m for m in activity.missing_elements())

    def test_subject_information_carries_the_rights(self):
        info = _compliant().subject_information()
        assert info["logic"]
        assert info["rights"]["human_intervention"]
        assert info["rights"]["contest_decision"]
        assert info["significant_effect"] is True


class TestRegistry:
    def test_registering_returns_the_activity(self):
        registry = AutomatedDecisionRegistry()
        activity = registry.register(_compliant())
        assert registry.get(activity.id) is activity
        assert registry.by_name("credit pre-screening") is activity

    def test_non_compliant_in_scope_activities_are_surfaced(self, capsys):
        registry = AutomatedDecisionRegistry()
        registry.register(_compliant())
        registry.register(AutomatedDecisionActivity(name="unguarded"))
        assert len(registry.all()) == 2
        assert len(registry.in_scope()) == 2
        non_compliant = registry.non_compliant()
        assert [a.name for a in non_compliant] == ["unguarded"]
        assert "missing safeguards" in capsys.readouterr().out

    def test_out_of_scope_activities_are_not_reported_as_non_compliant(self):
        registry = AutomatedDecisionRegistry()
        registry.register(
            AutomatedDecisionActivity(
                name="assisted", solely_automated=False, description="human decides"
            )
        )
        assert registry.non_compliant() == []
        assert registry.in_scope() == []

    def test_round_trips_through_its_dict_payload(self):
        activity = _compliant()
        restored = AutomatedDecisionActivity.from_dict(activity.to_dict())
        assert restored.to_dict() == activity.to_dict()
