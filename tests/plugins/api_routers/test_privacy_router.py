"""Tests for the privacy / DSR management router (scope-gated)."""

import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.api.errors import install_error_handlers
from core.auth.types import AuthRole, AuthUser
from core.middleware import require_user
from core.privacy import DataProviderRegistry, DataSubjectService, DictDataProvider
from plugins.api_routers import privacy as pr


def _service() -> DataSubjectService:
    reg = DataProviderRegistry()
    fb = DictDataProvider("feedback")
    fb.add("s1", {"q": "hi", "created_at": time.time()})
    reg.register(fb)
    return DataSubjectService(reg)


def _client(user: AuthUser, service: DataSubjectService) -> TestClient:
    app = FastAPI()
    app.include_router(pr.router)
    install_error_handlers(app)

    async def fake_require_user(request: Request) -> str:
        request.state.user = user
        return user.user_id

    app.dependency_overrides[require_user] = fake_require_user
    return TestClient(app)


ADMIN = AuthUser(user_id="a", roles={AuthRole.ADMIN})  # holds "*" → privacy:manage
NOSCOPE = AuthUser(user_id="u", roles={AuthRole.USER})
SCOPED = AuthUser(user_id="p", roles={AuthRole.USER}, scopes={"privacy:manage"})


@pytest.fixture
def service(monkeypatch):
    svc = _service()
    monkeypatch.setattr(pr, "get_data_subject_service", lambda: svc)
    return svc


def test_requires_scope(service):
    denied = _client(NOSCOPE, service).get("/privacy/providers")
    assert denied.status_code == 403
    r = _client(NOSCOPE, service).post("/privacy/export", json={"subject_id": "s1"})
    assert r.status_code == 403
    # RFC 9457 problem+json: stable code is a top-level extension member.
    assert r.json()["code"] == "insufficient_scope"


def test_admin_and_scoped_allowed(service):
    admin = _client(ADMIN, service).get("/privacy/providers")
    assert admin.status_code == 200
    scoped = _client(SCOPED, service).get("/privacy/providers")
    assert scoped.status_code == 200


def test_list_providers(service):
    r = _client(ADMIN, service).get("/privacy/providers")
    assert r.json()["providers"] == ["feedback"]


def test_export(service):
    r = _client(SCOPED, service).post("/privacy/export", json={"subject_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert body["subject_id"] == "s1"
    assert len(body["data"]["feedback"]) == 1


def test_erase(service):
    r = _client(SCOPED, service).post("/privacy/erase", json={"subject_id": "s1"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    # Idempotent: second erase removes nothing.
    r2 = _client(SCOPED, service).post("/privacy/erase", json={"subject_id": "s1"})
    assert r2.json()["total"] == 0


def test_retention_sweep(service):
    r = _client(ADMIN, service).post(
        "/privacy/retention/sweep", json={"older_than_days": 1}
    )
    assert r.status_code == 202
    assert "total" in r.json()


def test_export_validates_body(service):
    # Empty subject_id rejected by validation (422).
    r = _client(ADMIN, service).post("/privacy/export", json={"subject_id": ""})
    assert r.status_code == 422
