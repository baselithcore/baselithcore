"""Tests for the /compliance AI-governance API."""

from __future__ import annotations

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.api.errors import install_error_handlers
from core.auth.types import AuthRole, AuthUser
from core.compliance.documents import reset_document_services
from core.compliance.post_market import MonitoringMetric, PostMarketMonitoringPlan
from core.compliance.post_market_service import (
    get_post_market_service,
    reset_post_market_service,
)
from core.compliance.registry import get_ai_system_registry, reset_ai_system_registry
from core.middleware import require_user
from plugins.api_routers.compliance import router


@pytest.fixture(autouse=True)
def _clean_state():
    reset_ai_system_registry()
    reset_document_services()
    reset_post_market_service()
    yield
    reset_ai_system_registry()
    reset_document_services()
    reset_post_market_service()


def _client(scopes: set[str] | None = None) -> TestClient:
    user = AuthUser(
        user_id="dpo",
        roles={AuthRole.USER},
        scopes=scopes if scopes is not None else {"compliance:manage"},
    )
    app = FastAPI()
    # The scope choke point raises InsufficientScopeError; the real app maps it
    # to a 403 problem document, so the test app must install the same handlers.
    install_error_handlers(app)
    app.include_router(router)

    @app.middleware("http")
    async def _attach_user(request, call_next):
        request.state.user = user
        return await call_next(request)

    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def client():
    return _client()


class TestAuthorization:
    def test_missing_scope_is_rejected(self):
        resp = _client(scopes=set()).get("/compliance/systems")
        assert resp.status_code == 403

    def test_scope_grants_access(self, client):
        assert client.get("/compliance/systems").status_code == 200


class TestRegistry:
    def test_register_derives_the_risk_category(self, client):
        resp = client.post(
            "/compliance/systems",
            json={
                "name": "cv-screener",
                "annex_iii_areas": ["employment_and_worker_management"],
            },
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["system"]["risk_category"] == "high_risk"
        assert "Annex III" in " ".join(body["classification"]["citations"])

    def test_unknown_enum_value_is_a_400(self, client):
        resp = client.post(
            "/compliance/systems", json={"name": "x", "annex_iii_areas": ["nonsense"]}
        )
        assert resp.status_code == 400

    def test_prohibited_practice_is_recorded_not_rejected(self, client):
        resp = client.post(
            "/compliance/systems",
            json={"name": "scorer", "prohibited_practices": ["social_scoring"]},
        )
        assert resp.status_code == 201
        assert resp.json()["system"]["risk_category"] == "prohibited"

    def test_blocking_mode_refuses_a_prohibited_practice(self, client, monkeypatch):
        from core.config import compliance as compliance_config

        monkeypatch.setenv("COMPLIANCE_BLOCK_PROHIBITED_PRACTICES", "true")
        compliance_config._compliance_config = None
        try:
            resp = client.post(
                "/compliance/systems",
                json={"name": "scorer", "prohibited_practices": ["social_scoring"]},
            )
            assert resp.status_code == 422
            assert "Art. 5(1)(c)" in resp.json()["detail"]
        finally:
            compliance_config._compliance_config = None

    def test_get_system_returns_its_obligations(self, client):
        created = client.post(
            "/compliance/systems",
            json={"name": "s", "annex_iii_areas": ["law_enforcement"]},
        ).json()
        resp = client.get(f"/compliance/systems/{created['system']['id']}")
        assert resp.status_code == 200
        assert any("Art. 11" in o for o in resp.json()["obligations"])

    def test_unknown_system_is_a_404(self, client):
        assert client.get("/compliance/systems/nope").status_code == 404

    def test_lifecycle_transition_stamps_the_date(self, client):
        created = client.post("/compliance/systems", json={"name": "s"}).json()
        resp = client.post(
            f"/compliance/systems/{created['system']['id']}/lifecycle",
            json={"stage": "placed_on_market"},
        )
        assert resp.status_code == 200
        assert resp.json()["placed_on_market_at"] is not None

    def test_invalid_lifecycle_stage_is_a_400(self, client):
        created = client.post("/compliance/systems", json={"name": "s"}).json()
        resp = client.post(
            f"/compliance/systems/{created['system']['id']}/lifecycle",
            json={"stage": "teleported"},
        )
        assert resp.status_code == 400

    def test_reclassify_reflects_changed_facts(self, client):
        created = client.post("/compliance/systems", json={"name": "s"}).json()
        system_id = created["system"]["id"]
        # Mutate the stored record the way an operator would, then re-derive.
        anyio.run(
            lambda: _add_annex_iii(system_id, "administration_of_justice_and_"
                                   "democratic_processes")
        )
        resp = client.post(f"/compliance/systems/{system_id}/reclassify")
        assert resp.json()["system"]["risk_category"] == "high_risk"

    def test_summary_and_pending_registration(self, client):
        client.post(
            "/compliance/systems",
            json={"name": "hr", "annex_iii_areas": ["employment_and_worker_management"]},
        )
        summary = client.get("/compliance/summary").json()
        assert summary["total"] == 1
        assert summary["by_category"]["high_risk"] == 1
        pending = client.get("/compliance/pending-registration").json()
        assert pending["count"] == 1

    def test_filter_by_risk_category(self, client):
        client.post("/compliance/systems", json={"name": "bot",
                                                 "interacts_with_humans": True})
        resp = client.get("/compliance/systems", params={"risk_category": "limited_risk"})
        assert resp.json()["count"] == 1
        assert client.get(
            "/compliance/systems", params={"risk_category": "bogus"}
        ).status_code == 400


async def _add_annex_iii(system_id: str, area: str) -> None:
    from core.compliance.types import AnnexIIIArea

    registry = get_ai_system_registry()
    system = await registry.require(system_id)
    system.annex_iii_areas = [AnnexIIIArea(area)]


class TestDocuments:
    def test_draft_documentation_from_a_registered_system(self, client):
        created = client.post(
            "/compliance/systems", json={"name": "s", "intended_purpose": "rank"}
        ).json()
        resp = client.post(
            "/compliance/documentation/draft",
            params={"system_id": created["system"]["id"]},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["is_complete"] is False
        assert "development_process" in body["missing_sections"]

    def test_draft_for_unknown_system_is_a_404(self, client):
        resp = client.post(
            "/compliance/documentation/draft", params={"system_id": "nope"}
        )
        assert resp.status_code == 404

    def test_documentation_listing_can_filter_incomplete(self, client):
        created = client.post("/compliance/systems", json={"name": "s"}).json()
        client.post(
            "/compliance/documentation/draft",
            params={"system_id": created["system"]["id"]},
        )
        assert client.get("/compliance/documentation").json()["count"] == 1
        assert (
            client.get(
                "/compliance/documentation", params={"incomplete_only": True}
            ).json()["count"]
            == 1
        )

    def test_fria_and_ropa_listings_are_empty_by_default(self, client):
        assert client.get("/compliance/fria").json()["count"] == 0
        assert client.get("/compliance/ropa").json()["count"] == 0


class TestPostMarket:
    def _plan(self) -> PostMarketMonitoringPlan:
        return PostMarketMonitoringPlan(
            system_id="sys-1",
            objectives="drift",
            metrics=[MonitoringMetric(name="accuracy", threshold=0.9)],
            data_sources=["logs"],
            corrective_action_process="freeze",
            responsible_contacts=["ml-ops@example.test"],
        )

    def test_observe_flags_a_breach(self, client):
        plan = self._plan()
        anyio.run(lambda: get_post_market_service().save(plan))
        resp = client.post(
            f"/compliance/post-market/{plan.id}/observe",
            json={"metric": "accuracy", "value": 0.5},
        )
        assert resp.status_code == 200
        assert resp.json()["is_breach"] is True

    def test_undeclared_metric_is_a_400(self, client):
        plan = self._plan()
        anyio.run(lambda: get_post_market_service().save(plan))
        resp = client.post(
            f"/compliance/post-market/{plan.id}/observe",
            json={"metric": "nope", "value": 1.0},
        )
        assert resp.status_code == 400

    def test_unknown_plan_is_a_404(self, client):
        resp = client.post(
            "/compliance/post-market/nope/observe",
            json={"metric": "accuracy", "value": 1.0},
        )
        assert resp.status_code == 404

    def test_review_resets_the_cadence(self, client):
        plan = self._plan()
        anyio.run(lambda: get_post_market_service().save(plan))
        resp = client.post(f"/compliance/post-market/{plan.id}/review")
        assert resp.json()["last_reviewed_at"] is not None

    def test_listing_flags_overdue_reviews(self, client):
        from datetime import UTC, datetime, timedelta

        plan = self._plan()
        plan.created_at = datetime.now(UTC) - timedelta(days=400)
        anyio.run(lambda: get_post_market_service().save(plan))
        body = client.get("/compliance/post-market").json()
        assert body["plans"][0]["review_overdue"] is True


class TestPosture:
    def test_profile_endpoint_reports_without_enabling(self, client, monkeypatch):
        monkeypatch.setenv("BASELITH_COMPLIANCE_PROFILE", "ai-act-high-risk")
        body = client.get("/compliance/profile").json()
        assert body["profile"] == "ai-act-high-risk"
        assert body["satisfied"] is False
        assert "AUDIT_ENABLED" in body["gaps"]

    def test_audit_verify_reports_when_no_durable_sink(self, client):
        from core.observability.audit import reset_audit_logger

        reset_audit_logger()
        try:
            body = client.get("/compliance/audit/verify").json()
            assert body["ok"] is None
            assert "AUDIT_DB_PATH" in body["reason"]
        finally:
            reset_audit_logger()
