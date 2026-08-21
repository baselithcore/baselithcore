"""Tests for the cross-artefact governance review sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from core.compliance.artefact_services import (
    get_dpia_service,
    get_risk_management_service,
    reset_artefact_services,
)
from core.compliance.post_market_service import (
    get_post_market_service,
    reset_post_market_service,
)
from core.compliance.review_sweep import ComplianceReviewScheduler, sweep_summary
from tests.unit.core.compliance.test_artefacts import (
    _complete_dpia,
    _complete_risk_file,
)
from tests.unit.core.compliance.test_post_market_service import _plan


@pytest.fixture(autouse=True)
def _clean_state():
    reset_artefact_services()
    reset_post_market_service()
    yield
    reset_artefact_services()
    reset_post_market_service()


class TestCoverage:
    async def test_a_clean_estate_reports_nothing(self):
        findings = await ComplianceReviewScheduler().sweep()
        assert all(not v for v in findings.values())
        assert sweep_summary(findings)["needs_attention"] is False

    async def test_an_overdue_risk_file_is_reported(self):
        file = _complete_risk_file()
        file.created_at = datetime.now(UTC) - timedelta(days=400)
        await get_risk_management_service().save(file)

        # Patch the module logger instead of capturing stdout: the global
        # structlog sink is process-wide mutable state, so a stdout assert is
        # order-dependent under random test ordering.
        with patch("core.compliance.review_sweep.logger") as log:
            findings = await ComplianceReviewScheduler().sweep()
        assert findings["overdue_risk_reviews"] == [file.id]
        # The article behind the warning must be in the line, not just the id.
        logged = " ".join(str(c) for c in log.warning.call_args_list)
        assert "Art. 9(1)" in logged
        assert sweep_summary(findings)["needs_attention"] is True

    async def test_an_overdue_post_market_plan_is_reported(self):
        plan = _plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=400)
        await get_post_market_service().save(plan)
        findings = await ComplianceReviewScheduler().sweep()
        assert findings["overdue_post_market_reviews"] == [plan.id]

    async def test_a_dpia_awaiting_prior_consultation_is_reported(self):
        service = get_dpia_service()
        dpia = await service.save(_complete_dpia(residual_high=True))
        await service.complete(dpia.id)

        with patch("core.compliance.review_sweep.logger") as log:
            findings = await ComplianceReviewScheduler().sweep()
        assert findings["blocked_dpias"] == [dpia.id]
        logged = " ".join(str(c) for c in log.warning.call_args_list)
        assert "Art. 36(1)" in logged

    async def test_prior_consultation_clears_the_block(self):
        service = get_dpia_service()
        dpia = await service.save(_complete_dpia(residual_high=True))
        await service.complete(dpia.id)
        await service.record_prior_consultation(dpia.id)
        findings = await ComplianceReviewScheduler().sweep()
        assert findings["blocked_dpias"] == []

    async def test_incomplete_artefacts_are_listed_without_urgency(self):
        from core.compliance.dpia import DataProtectionImpactAssessment
        from core.compliance.risk_management import RiskManagementSystem

        await get_risk_management_service().save(RiskManagementSystem(system_id="s"))
        await get_dpia_service().save(DataProtectionImpactAssessment(name="x"))

        findings = await ComplianceReviewScheduler().sweep()
        assert len(findings["incomplete_risk_files"]) == 1
        assert len(findings["incomplete_dpias"]) == 1
        # An incomplete draft is not an overdue review — it must not raise the
        # urgency flag on its own.
        assert findings["overdue_risk_reviews"] == []
        assert sweep_summary(findings)["needs_attention"] is True  # blocked DPIA

    async def test_all_three_subsystems_report_together(self):
        risk = _complete_risk_file()
        risk.created_at = datetime.now(UTC) - timedelta(days=400)
        await get_risk_management_service().save(risk)
        plan = _plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=400)
        await get_post_market_service().save(plan)
        service = get_dpia_service()
        dpia = await service.save(_complete_dpia(residual_high=True))
        await service.complete(dpia.id)

        findings = await ComplianceReviewScheduler().sweep()
        assert findings["overdue_risk_reviews"] == [risk.id]
        assert findings["overdue_post_market_reviews"] == [plan.id]
        assert findings["blocked_dpias"] == [dpia.id]


class TestResilience:
    async def test_one_broken_subsystem_does_not_hide_the_others(self, monkeypatch):
        plan = _plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=400)
        await get_post_market_service().save(plan)

        def _boom():
            raise RuntimeError("store offline")

        monkeypatch.setattr(
            "core.compliance.artefact_services.get_risk_management_service", _boom
        )
        findings = await ComplianceReviewScheduler().sweep()
        # The risk sweep failed; the post-market finding still came through.
        assert findings["overdue_post_market_reviews"] == [plan.id]
        assert findings["overdue_risk_reviews"] == []

    async def test_summary_counts_every_bucket(self):
        findings = await ComplianceReviewScheduler().sweep()
        summary = sweep_summary(findings)
        assert set(summary["counts"]) == set(findings)
