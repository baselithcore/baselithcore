"""Tests for the Art. 9 / Art. 13 / DPIA / Art. 22 compliance endpoints."""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.api.errors import install_error_handlers
from core.auth.types import AuthRole, AuthUser
from core.compliance.artefact_services import (
    get_dpia_service,
    get_risk_management_service,
    reset_artefact_services,
)
from core.compliance.post_market_service import reset_post_market_service
from core.compliance.registry import get_ai_system_registry, reset_ai_system_registry
from core.compliance.types import AiSystem
from core.middleware import require_user
from core.privacy.automated_decisions import (
    Art22Ground,
    AutomatedDecisionActivity,
    get_automated_decision_registry,
    reset_automated_decision_registry,
)
from plugins.api_routers.compliance import router
from tests.unit.core.compliance.test_artefacts import (  # reuse the fixtures
    _complete_dpia,
    _complete_risk_file,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_ai_system_registry()
    reset_artefact_services()
    reset_post_market_service()
    reset_automated_decision_registry()
    yield
    reset_ai_system_registry()
    reset_artefact_services()
    reset_post_market_service()
    reset_automated_decision_registry()


@pytest.fixture
def client():
    user = AuthUser(
        user_id="dpo", roles={AuthRole.USER}, scopes={"compliance:manage"}
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)

    @app.middleware("http")
    async def _attach_user(request, call_next):
        request.state.user = user
        return await call_next(request)

    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


class TestRiskManagement:
    def test_listing_flags_overdue_reviews(self, client):
        from datetime import UTC, datetime, timedelta

        file = _complete_risk_file()
        file.created_at = datetime.now(UTC) - timedelta(days=400)
        anyio.run(lambda: get_risk_management_service().save(file))
        body = client.get("/compliance/risk-management").json()
        assert body["count"] == 1
        assert body["files"][0]["review_overdue"] is True

    def test_get_renders_annex_iv_section_five(self, client):
        file = _complete_risk_file()
        anyio.run(lambda: get_risk_management_service().save(file))
        body = client.get(f"/compliance/risk-management/{file.id}").json()
        assert "under-detection" in body["markdown"]

    def test_unknown_file_is_a_404(self, client):
        assert client.get("/compliance/risk-management/nope").status_code == 404

    def test_review_over_open_risks_is_a_422(self, client):
        from core.compliance.risk_management import RiskManagementSystem
        from tests.unit.core.compliance.test_artefacts import _closed_risk

        file = RiskManagementSystem(system_id="s", risks=[_closed_risk()])
        anyio.run(lambda: get_risk_management_service().save(file))
        resp = client.post(f"/compliance/risk-management/{file.id}/review")
        assert resp.status_code == 422
        assert "risks remain open" in resp.json()["detail"]

    def test_review_succeeds_on_a_closed_file(self, client):
        file = _complete_risk_file()
        anyio.run(lambda: get_risk_management_service().save(file))
        resp = client.post(f"/compliance/risk-management/{file.id}/review")
        assert resp.status_code == 200
        assert resp.json()["last_reviewed_at"] is not None


class TestInstructions:
    def _register(self) -> str:
        system = AiSystem(
            name="cv-screener",
            intended_purpose="Rank applications",
            provider_name="Acme",
            human_oversight_contacts=["lead@acme.test"],
        )
        anyio.run(lambda: get_ai_system_registry().register(system))
        return system.id

    def test_draft_pulls_from_the_registry(self, client):
        system_id = self._register()
        resp = client.post(
            "/compliance/instructions/draft",
            json={"system_id": system_id, "provider_contact": "dpo@acme.test"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["intended_purpose"] == "Rank applications"
        assert body["provider_contact"] == "dpo@acme.test"
        assert body["is_complete"] is False

    def test_draft_can_pull_the_risk_file(self, client):
        system_id = self._register()
        file = _complete_risk_file()
        anyio.run(lambda: get_risk_management_service().save(file))
        resp = client.post(
            "/compliance/instructions/draft",
            json={"system_id": system_id, "risk_file_id": file.id},
        )
        assert "under-detection" in resp.json()["risk_circumstances"]

    def test_draft_for_unknown_system_is_a_404(self, client):
        resp = client.post(
            "/compliance/instructions/draft", json={"system_id": "nope"}
        )
        assert resp.status_code == 404

    def test_draft_with_unknown_risk_file_is_a_404(self, client):
        system_id = self._register()
        resp = client.post(
            "/compliance/instructions/draft",
            json={"system_id": system_id, "risk_file_id": "nope"},
        )
        assert resp.status_code == 404

    def test_issuing_incomplete_instructions_is_a_422(self, client):
        system_id = self._register()
        created = client.post(
            "/compliance/instructions/draft", json={"system_id": system_id}
        ).json()
        resp = client.post(f"/compliance/instructions/{created['id']}/issue")
        assert resp.status_code == 422
        assert "Art. 13(3)" in resp.json()["detail"]

    def test_get_renders_the_deployer_document(self, client):
        system_id = self._register()
        created = client.post(
            "/compliance/instructions/draft", json={"system_id": system_id}
        ).json()
        body = client.get(f"/compliance/instructions/{created['id']}").json()
        assert "Instructions for use" in body["markdown"]

    def test_incomplete_filter(self, client):
        system_id = self._register()
        client.post("/compliance/instructions/draft", json={"system_id": system_id})
        assert (
            client.get(
                "/compliance/instructions", params={"incomplete_only": True}
            ).json()["count"]
            == 1
        )


class TestDpiaEndpoints:
    def test_completing_a_partial_dpia_is_a_422(self, client):
        from core.compliance.dpia import DataProtectionImpactAssessment

        dpia = DataProtectionImpactAssessment(name="x")
        anyio.run(lambda: get_dpia_service().save(dpia))
        resp = client.post(f"/compliance/dpia/{dpia.id}/complete")
        assert resp.status_code == 422
        assert "Art. 35(7)" in resp.json()["detail"]

    def test_high_residual_risk_blocks_then_unblocks(self, client):
        dpia = _complete_dpia(residual_high=True)
        anyio.run(lambda: get_dpia_service().save(dpia))
        completed = client.post(f"/compliance/dpia/{dpia.id}/complete").json()
        assert completed["may_start_processing"] is False
        assert client.get(
            "/compliance/dpia", params={"blocked_only": True}
        ).json()["count"] == 1

        consulted = client.post(
            f"/compliance/dpia/{dpia.id}/prior-consultation"
        ).json()
        assert consulted["may_start_processing"] is True

    def test_unknown_dpia_is_a_404(self, client):
        assert client.post("/compliance/dpia/nope/complete").status_code == 404


class TestAutomatedDecisions:
    def test_non_compliant_filter_surfaces_missing_safeguards(self, client):
        registry = get_automated_decision_registry()
        registry.register(AutomatedDecisionActivity(name="unguarded"))
        body = client.get(
            "/compliance/automated-decisions", params={"non_compliant_only": True}
        ).json()
        assert body["count"] == 1
        assert any("Art. 22(2)" in m for m in body["activities"][0]["missing_elements"])

    def test_subject_information_endpoint(self, client):
        activity = get_automated_decision_registry().register(
            AutomatedDecisionActivity(
                name="scoring",
                ground=Art22Ground.CONTRACT,
                human_intervention_channel="email",
                express_view_channel="form",
                contest_channel="appeal",
                logic_explanation="weighted score",
                significance_and_consequences="delays the application",
            )
        )
        body = client.get(
            f"/compliance/automated-decisions/{activity.id}/subject-information"
        ).json()
        assert body["logic"] == "weighted score"
        assert body["rights"]["contest_decision"] == "appeal"

    def test_unknown_activity_is_a_404(self, client):
        resp = client.get(
            "/compliance/automated-decisions/nope/subject-information"
        )
        assert resp.status_code == 404
