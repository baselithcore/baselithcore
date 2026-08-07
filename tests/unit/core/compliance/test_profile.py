"""Tests for the compliance profile gate."""

from __future__ import annotations

import pytest

from core.compliance.profile import (
    ComplianceProfile,
    ComplianceProfileError,
    enforce_profile,
    evaluate_profile,
)
from core.config.audit import reset_audit_config
from core.config.compliance import reset_compliance_config


@pytest.fixture(autouse=True)
def _clean_config():
    reset_audit_config()
    reset_compliance_config()
    yield
    reset_audit_config()
    reset_compliance_config()


class TestProfileResolution:
    def test_off_profile_imposes_nothing(self):
        report = evaluate_profile(ComplianceProfile.OFF)
        assert report.requirements == []
        assert report.satisfied is True

    def test_unknown_profile_falls_back_to_off(self):
        assert evaluate_profile("nonsense").profile is ComplianceProfile.OFF

    def test_profile_is_read_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("BASELITH_COMPLIANCE_PROFILE", "nis2")
        assert evaluate_profile().profile is ComplianceProfile.NIS2

    def test_string_profiles_are_accepted(self):
        assert (
            evaluate_profile("ai-act-high-risk").profile
            is ComplianceProfile.AI_ACT_HIGH_RISK
        )


class TestRequirements:
    def test_default_deployment_has_gaps_under_a_real_profile(self, monkeypatch):
        monkeypatch.delenv("AUDIT_ENABLED", raising=False)
        reset_audit_config()
        report = evaluate_profile(ComplianceProfile.AI_ACT_HIGH_RISK)
        assert report.satisfied is False
        assert "AUDIT_ENABLED" in [g.setting for g in report.gaps]

    def test_high_risk_requires_the_ai_act_incident_clock(self):
        settings = [
            r.setting
            for r in evaluate_profile(ComplianceProfile.AI_ACT_HIGH_RISK).requirements
        ]
        assert "AI_ACT_INCIDENT_REPORTING_ENABLED" in settings
        assert "COMPLIANCE_ENABLED" in settings

    def test_gdpr_profile_requires_the_breach_clock_not_the_nis2_one(self):
        settings = [
            r.setting for r in evaluate_profile(ComplianceProfile.GDPR).requirements
        ]
        assert "GDPR_BREACH_REPORTING_ENABLED" in settings
        assert "INCIDENT_REPORTING_ENABLED" not in settings

    def test_full_profile_covers_every_regime(self):
        settings = [
            r.setting for r in evaluate_profile(ComplianceProfile.FULL).requirements
        ]
        for setting in (
            "INCIDENT_REPORTING_ENABLED",
            "DORA_INCIDENT_REPORTING_ENABLED",
            "AI_ACT_INCIDENT_REPORTING_ENABLED",
            "GDPR_BREACH_REPORTING_ENABLED",
            "PRIVACY_ENABLED",
            "TRANSPARENCY_ENABLED",
        ):
            assert setting in settings

    def test_retention_below_the_floor_is_a_gap(self, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        monkeypatch.setenv("AUDIT_DB_PATH", "/tmp/audit.db")
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "30")
        reset_audit_config()
        gaps = [g.setting for g in evaluate_profile(ComplianceProfile.NIS2).gaps]
        assert "AUDIT_RETENTION_DAYS" in gaps

    def test_keep_forever_retention_satisfies_the_floor(self, monkeypatch):
        monkeypatch.setenv("AUDIT_ENABLED", "true")
        monkeypatch.setenv("AUDIT_DB_PATH", "/tmp/audit.db")
        monkeypatch.setenv("AUDIT_RETENTION_DAYS", "0")
        reset_audit_config()
        gaps = [g.setting for g in evaluate_profile(ComplianceProfile.NIS2).gaps]
        assert "AUDIT_RETENTION_DAYS" not in gaps


class TestEnforcement:
    def test_gaps_only_warn_by_default(self, monkeypatch):
        monkeypatch.delenv("BASELITH_COMPLIANCE_PROFILE_STRICT", raising=False)
        report = enforce_profile(ComplianceProfile.AI_ACT_HIGH_RISK)
        assert report.satisfied is False

    def test_strict_mode_raises_on_a_gap(self, monkeypatch):
        monkeypatch.setenv("BASELITH_COMPLIANCE_PROFILE_STRICT", "true")
        with pytest.raises(ComplianceProfileError) as excinfo:
            enforce_profile(ComplianceProfile.AI_ACT_HIGH_RISK)
        assert "not satisfied" in str(excinfo.value)
        assert excinfo.value.report.gaps

    def test_strict_mode_is_silent_when_off_profile(self, monkeypatch):
        monkeypatch.setenv("BASELITH_COMPLIANCE_PROFILE_STRICT", "true")
        assert enforce_profile(ComplianceProfile.OFF).satisfied is True

    def test_the_profile_never_flips_a_setting_on(self, monkeypatch):
        monkeypatch.delenv("AUDIT_ENABLED", raising=False)
        reset_audit_config()
        enforce_profile(ComplianceProfile.FULL)
        from core.config.audit import get_audit_config

        # Reporting a gap must not have enabled anything behind the operator.
        assert get_audit_config().enabled is False
