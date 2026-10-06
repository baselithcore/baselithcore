"""Response models on the runs, approvals, webhooks and tenant routes; WS on /v1.

Same contract as :mod:`.test_api_routers_response_models`: the models type the
OpenAPI document without changing a byte of the response body. The last test
covers the WebSocket chat channel, now served at ``/v1/chat/ws`` as well as at
the deprecated unprefixed ``/chat/ws``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import core.chat as chat_package
import plugins.api_routers.approvals as approvals_module
import plugins.api_routers.runs as runs_module
import plugins.api_routers.webhooks as webhooks_module
from core.auth.types import AuthRole, AuthUser
from core.config.webhooks import WebhookConfig
from core.context import set_tenant_context
from core.middleware import require_user
from core.orchestration.checkpoint import (
    STATUS_AWAITING_APPROVAL,
    Checkpoint,
    InMemoryCheckpointStore,
)
from core.webhooks.dispatcher import WebhookDispatcher
from core.webhooks.service import WebhookService
from core.webhooks.store import InMemoryWebhookStore
from core.webhooks.types import WebhookDelivery
from plugins.api_routers.admin import verify_credentials
from plugins.api_routers.tenant import router as tenant_router
from tests.unit.plugins_tests._wire_helpers import (
    WireSpy,
    assert_problem_documented,
    assert_typed,
)

pytestmark = [pytest.mark.unit]

_USER = AuthUser(
    user_id="w",
    roles={AuthRole.USER},
    scopes={"webhooks:read", "webhooks:write"},
)


def _client(*routers: Any) -> TestClient:
    app = FastAPI()
    for router in routers:
        app.include_router(router)

    async def fake_require_user(request: Request) -> str:
        request.state.user = _USER
        set_tenant_context(_USER.tenant_id)
        return _USER.user_id

    app.dependency_overrides[require_user] = fake_require_user
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    return TestClient(app)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> WireSpy:
    return WireSpy(monkeypatch)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> InMemoryCheckpointStore:
    checkpoints = InMemoryCheckpointStore(history_enabled=True)
    for module in (runs_module, approvals_module):
        monkeypatch.setattr(module, "get_default_checkpoint_store", lambda: checkpoints)
    return checkpoints


async def _seed(store: InMemoryCheckpointStore) -> None:
    run = Checkpoint(run_id="run-1", tenant_id="t-1", query="wipe", intent="ops")
    run.trajectory = [{"tool": "read", "ok": True}]
    run.steps = {"s1": {"tool_name": "read", "args": {}, "result": 1, "at": 1.5}}
    await store.save(run)  # v1
    run.step = 1
    run.status = STATUS_AWAITING_APPROVAL
    run.pending_approval = {"tool": "wipe", "category": "destructive"}
    await store.save(run)  # v2


class TestRuns:
    def test_bodies_unchanged(
        self, spy: WireSpy, store: InMemoryCheckpointStore
    ) -> None:
        anyio.run(_seed, store)
        client = _client(runs_module.router)
        for method, path, body in [
            ("get", "/runs/run-1/history", None),
            ("get", "/runs/run-1/history?limit=1", None),
            ("get", "/runs/run-1/history/2", None),
            ("post", "/runs/run-1/fork", {"version": 1, "new_run_id": "fork-1"}),
        ]:
            resp = client.request(method, path, json=body)
            assert resp.status_code == 200, (path, resp.text)
            spy.assert_unchanged(resp)


class TestApprovals:
    def test_bodies_unchanged(
        self,
        spy: WireSpy,
        store: InMemoryCheckpointStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        anyio.run(_seed, store)
        agent = AsyncMock()
        agent.process = AsyncMock(return_value={"response": "done", "cost": 0})
        monkeypatch.setattr(
            chat_package, "chat_service", type("S", (), {"agent": agent})()
        )
        client = _client(approvals_module.router)
        for method, path, body in [
            ("get", "/approvals", None),
            ("get", "/approvals?limit=1", None),
            ("post", "/approvals/run-1/decision", {"approved": True, "reason": "ok"}),
            ("post", "/approvals/run-1/resume", None),
        ]:
            resp = client.request(method, path, json=body)
            assert resp.status_code == 200, (path, resp.text)
            spy.assert_unchanged(resp)


class TestWebhooks:
    @pytest.fixture
    def service(self, monkeypatch: pytest.MonkeyPatch) -> WebhookService:
        cfg = WebhookConfig(WEBHOOKS_ENABLED=True, WEBHOOK_ALLOW_INTERNAL=True)
        hooks = InMemoryWebhookStore()
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200))
        )
        svc = WebhookService(
            store=hooks, config=cfg, dispatcher=WebhookDispatcher(hooks, cfg, http)
        )
        monkeypatch.setattr(webhooks_module, "get_webhook_service", lambda: svc)
        return svc

    def test_bodies_unchanged(self, spy: WireSpy, service: WebhookService) -> None:
        client = _client(webhooks_module.router)
        created = client.post(
            "/webhooks",
            json={
                "url": "https://hooks.test/r",
                "event_types": ["a.b", "c.d"],
                "headers": {"X-Route": "1"},
            },
        )
        assert created.status_code == 201
        endpoint_id = created.json()["endpoint"]["id"]
        stored = anyio.run(service.store.get_endpoint, endpoint_id)
        assert stored is not None
        # The endpoint view is the redacted dump, key for key.
        before = {"endpoint": stored.redacted(), "secret": created.json()["secret"]}
        assert created.json() == spy.returned[-1].model_dump(mode="json")
        assert list(created.json()["endpoint"]) == list(before["endpoint"])

        delivery = WebhookDelivery(
            endpoint_id=endpoint_id,
            event_id="evt_1",
            event_type="a.b",
            tenant_id=_USER.tenant_id,
            url="https://hooks.test/r",
            last_status_code=500,
            payload={"data": {"k": 1}},
        )
        anyio.run(service.store.record_delivery, delivery)
        for method, path in [
            ("get", "/webhooks"),
            ("get", "/webhooks/deliveries?limit=1"),
            ("post", f"/webhooks/deliveries/{delivery.id}/replay"),
            ("delete", f"/webhooks/{endpoint_id}"),
        ]:
            resp = client.request(method, path)
            assert resp.status_code == 200, (path, resp.text)
            spy.assert_unchanged(resp)


def test_openapi_documents_typed_2xx_and_problem_errors() -> None:
    openapi = _client(
        runs_module.router,
        approvals_module.router,
        webhooks_module.router,
        tenant_router,
    ).app.openapi()
    for path, method in [
        ("/runs/{run_id}/history", "get"),
        ("/runs/{run_id}/history/{version}", "get"),
        ("/runs/{run_id}/fork", "post"),
        ("/approvals", "get"),
        ("/approvals/{run_id}/decision", "post"),
        ("/approvals/{run_id}/resume", "post"),
        ("/webhooks", "post"),
        ("/webhooks", "get"),
        ("/webhooks/{endpoint_id}", "delete"),
        ("/webhooks/deliveries", "get"),
        ("/webhooks/deliveries/{delivery_id}/replay", "post"),
        ("/admin/tenants", "get"),
        ("/admin/tenants", "post"),
    ]:
        assert_typed(openapi, path, method)
        assert_problem_documented(openapi, path, method, "401")
    assert_problem_documented(openapi, "/runs/{run_id}/fork", "post", "404")
    assert_problem_documented(openapi, "/webhooks", "post", "409")
    endpoint = openapi["components"]["schemas"]["WebhookEndpointView"]
    assert "secret" not in endpoint["properties"]


def _plugin_ws_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The plugin's routers, with the chat_ws gate and chat service stubbed."""
    from plugins.api_routers.plugin import ApiRoutersPlugin
    from tests.unit.plugins_tests.test_chat_ws import _client as ws_client

    # The chat_ws helper stubs credentials, gate and chat service; its
    # single-router client is not used — the plugin's routers are.
    ws_client(monkeypatch, authenticated=True, chunks=["Hel", "lo"])
    app = FastAPI()
    for router in ApiRoutersPlugin().get_routers():
        app.include_router(router)
    return TestClient(app)


def _chat_turn(client: TestClient, path: str) -> list[dict[str, Any]]:
    with client.websocket_connect(path, headers={"x-api-key": "k"}) as ws:
        ws.send_json({"query": "hi"})
        frames: list[dict[str, Any]] = []
        while not frames or frames[-1]["type"] not in ("final", "error"):
            frames.append(ws.receive_json())
    return frames


class TestWebSocketVersioning:
    def test_both_paths_connect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_V1_ENABLED", raising=False)
        client = _plugin_ws_client(monkeypatch)
        for path in ("/v1/chat/ws", "/chat/ws"):
            frames = _chat_turn(client, path)
            assert frames[-1]["type"] == "final", (path, frames)
            assert [f["content"] for f in frames[:-1]] == ["Hel", "lo"]

    def test_v1_disabled_serves_only_the_unprefixed_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from starlette.websockets import WebSocketDisconnect

        monkeypatch.setenv("API_V1_ENABLED", "false")
        client = _plugin_ws_client(monkeypatch)
        assert _chat_turn(client, "/chat/ws")[-1]["type"] == "final"
        with pytest.raises(WebSocketDisconnect):
            _chat_turn(client, "/v1/chat/ws")
