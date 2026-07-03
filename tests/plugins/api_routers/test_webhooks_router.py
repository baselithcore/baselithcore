"""Tests for the webhook management router (scope-gated CRUD)."""

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.api.errors import install_error_handlers
from core.auth.types import AuthRole, AuthUser
from core.config.webhooks import WebhookConfig
from core.context import set_tenant_context
from core.middleware import require_user
from core.webhooks.dispatcher import WebhookDispatcher
from core.webhooks.service import WebhookService
from core.webhooks.store import InMemoryWebhookStore
from plugins.api_routers import webhooks as wh

HOOK_URL = "https://hooks.test/receiver"


def _service() -> WebhookService:
    cfg = WebhookConfig(WEBHOOKS_ENABLED=True, WEBHOOK_ALLOW_INTERNAL=True)
    store = InMemoryWebhookStore()
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200))
    )
    return WebhookService(
        store=store, config=cfg, dispatcher=WebhookDispatcher(store, cfg, client)
    )


def _client(user: AuthUser, service: WebhookService) -> TestClient:
    app = FastAPI()
    app.include_router(wh.router)
    install_error_handlers(app)

    async def fake_require_user(request: Request) -> str:
        request.state.user = user
        # Mirror enforce_auth: bind the request's tenant context to the user.
        set_tenant_context(user.tenant_id)
        return user.user_id

    app.dependency_overrides[require_user] = fake_require_user
    return TestClient(app)


# Identities: one with webhook scopes, one without.
WRITER = AuthUser(
    user_id="w", roles={AuthRole.USER}, scopes={"webhooks:read", "webhooks:write"}
)
READER = AuthUser(user_id="r", roles={AuthRole.USER}, scopes={"webhooks:read"})
NOSCOPE = AuthUser(user_id="n", roles={AuthRole.USER})
ADMIN = AuthUser(user_id="a", roles={AuthRole.ADMIN})


@pytest.fixture
def service(monkeypatch):
    svc = _service()
    monkeypatch.setattr(wh, "get_webhook_service", lambda: svc)
    return svc


def test_create_requires_write_scope(service):
    c = _client(NOSCOPE, service)
    r = c.post("/webhooks", json={"url": HOOK_URL})
    assert r.status_code == 403
    # RFC 9457 problem+json: stable code is a top-level extension member.
    assert r.json()["code"] == "insufficient_scope"


def test_create_returns_secret_once(service):
    c = _client(WRITER, service)
    r = c.post("/webhooks", json={"url": HOOK_URL, "event_types": ["chat.done"]})
    assert r.status_code == 201
    body = r.json()
    assert body["secret"].startswith("whsec_")
    assert "secret" not in body["endpoint"]  # redacted
    assert body["endpoint"]["has_secret"] is True


def test_admin_has_implicit_scope(service):
    # ADMIN holds the "*" scope, so no explicit webhooks:* needed.
    c = _client(ADMIN, service)
    resp = c.post("/webhooks", json={"url": HOOK_URL})
    assert resp.status_code == 201


def test_list_requires_read_scope(service):
    noscope = _client(NOSCOPE, service).get("/webhooks")
    assert noscope.status_code == 403
    reader = _client(READER, service).get("/webhooks")
    assert reader.status_code == 200


def test_list_returns_registered(service):
    c = _client(WRITER, service)
    c.post("/webhooks", json={"url": HOOK_URL})
    r = c.get("/webhooks")
    assert r.status_code == 200
    assert len(r.json()["endpoints"]) == 1


def test_delete_unknown_404(service):
    c = _client(WRITER, service)
    resp = c.delete("/webhooks/whe_missing")
    assert resp.status_code == 404


def test_delete_existing(service):
    c = _client(WRITER, service)
    created = c.post("/webhooks", json={"url": HOOK_URL}).json()
    endpoint_id = created["endpoint"]["id"]
    r = c.delete(f"/webhooks/{endpoint_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"


def test_reader_cannot_delete(service):
    c = _client(READER, service)
    resp = c.delete("/webhooks/whe_x")
    assert resp.status_code == 403


def test_deliveries_list_read_scope(service):
    c = _client(READER, service)
    r = c.get("/webhooks/deliveries")
    assert r.status_code == 200
    assert r.json()["deliveries"] == []


def test_replay_unknown_404(service):
    c = _client(WRITER, service)
    resp = c.post("/webhooks/deliveries/whd_x/replay")
    assert resp.status_code == 404


def test_cannot_delete_other_tenants_endpoint(service):
    # Tenant A registers an endpoint.
    writer_a = AuthUser(
        user_id="a", tenant_id="acme", roles={AuthRole.USER}, scopes={"webhooks:write"}
    )
    created = (
        _client(writer_a, service).post("/webhooks", json={"url": HOOK_URL}).json()
    )
    endpoint_id = created["endpoint"]["id"]

    # Tenant B (same scope) must not be able to delete A's endpoint → 404.
    writer_b = AuthUser(
        user_id="b", tenant_id="other", roles={AuthRole.USER}, scopes={"webhooks:write"}
    )
    denied = _client(writer_b, service).delete(f"/webhooks/{endpoint_id}")
    assert denied.status_code == 404
    # Owner still can.
    owned = _client(writer_a, service).delete(f"/webhooks/{endpoint_id}")
    assert owned.status_code == 200
