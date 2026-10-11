"""Tests for the privacy / DSR management router (scope-gated)."""

import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from baselith.core.api.errors import install_error_handlers
from baselith.core.auth.types import AuthRole, AuthUser
from baselith.core.middleware import require_user
from baselith.core.privacy import (
    DataProviderRegistry,
    DataSubjectService,
    DictDataProvider,
)
from baselith.plugins.api_routers import privacy as pr


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


# --- Art. 16 / 18 / 21 and Art. 7 consent routes ------------------------------


@pytest.fixture
def consent(monkeypatch):
    from baselith.core.privacy.consent import ConsentService

    svc = ConsentService()
    monkeypatch.setattr(pr, "get_consent_service", lambda: svc)
    return svc


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/privacy/rectify", {"subject_id": "s1", "corrections": {"q": "x"}}),
        ("post", "/privacy/restrict", {"subject_id": "s1"}),
        ("post", "/privacy/object", {"subject_id": "s1", "direct_marketing": True}),
        ("get", "/privacy/consent/s1", None),
        ("post", "/privacy/consent/grant", {"subject_id": "s1", "purpose": "news"}),
        ("post", "/privacy/consent/withdraw", {"subject_id": "s1", "purpose": "news"}),
    ],
)
def test_rights_routes_require_the_scope(service, consent, method, path, body):
    client = _client(NOSCOPE, service)
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 403
    assert response.json()["code"] == "insufficient_scope"


def test_rectify_reports_per_provider(service):
    r = _client(SCOPED, service).post(
        "/privacy/rectify", json={"subject_id": "s1", "corrections": {"q": "fixed"}}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["subject_id"] == "s1"
    assert body["rectified"]["feedback"] == 1
    assert body["total"] == 1


def test_rectify_needs_corrections(service):
    r = _client(ADMIN, service).post(
        "/privacy/rectify", json={"subject_id": "s1", "corrections": {}}
    )
    assert r.status_code == 422


def test_restrict_and_release(service):
    client = _client(ADMIN, service)
    restricted = client.post("/privacy/restrict", json={"subject_id": "s1"})
    assert restricted.status_code == 200
    assert restricted.json()["restricted"] is True
    released = client.post(
        "/privacy/restrict", json={"subject_id": "s1", "restricted": False}
    )
    assert released.json()["restricted"] is False


def test_direct_marketing_objection_is_upheld(service):
    r = _client(ADMIN, service).post(
        "/privacy/object",
        json={
            "subject_id": "s1",
            "direct_marketing": True,
            "override_grounds": "legitimate interest",
        },
    )
    assert r.status_code == 200
    assert r.json()["subject_id"] == "s1"


def test_consent_grant_prove_withdraw(service, consent):
    client = _client(ADMIN, service)
    granted = client.post(
        "/privacy/consent/grant",
        json={"subject_id": "s1", "purpose": "news", "notice_version": "v2"},
    )
    assert granted.status_code == 201

    proof = client.get("/privacy/consent/s1").json()
    assert proof["active_purposes"] == ["news"]
    assert len(proof["history"]) == 1

    withdrawn = client.post(
        "/privacy/consent/withdraw", json={"subject_id": "s1", "purpose": "news"}
    )
    assert withdrawn.status_code == 200
    proof = client.get("/privacy/consent/s1").json()
    assert proof["active_purposes"] == []
    # Withdrawal appends to the proof chain; it never deletes the grant.
    assert len(proof["history"]) == 2


def test_withdraw_without_consent_is_404(service, consent):
    r = _client(ADMIN, service).post(
        "/privacy/consent/withdraw", json={"subject_id": "s1", "purpose": "none"}
    )
    assert r.status_code == 404


def test_control_characters_in_a_subject_id_are_refused(service, consent):
    client = _client(ADMIN, service)
    r = client.post(
        "/privacy/consent/grant", json={"subject_id": "acme\x1fs1", "purpose": "p"}
    )
    assert r.status_code == 422
    r = client.get("/privacy/consent/acme%1Fs1")
    assert r.status_code == 422
