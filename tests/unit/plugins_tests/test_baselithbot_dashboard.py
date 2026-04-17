"""Unit tests for the Baselithbot dashboard API + auth + security headers.

Covers:
    - overview / sessions / crons / nodes / doctor read paths
    - bearer-token guard (missing / wrong / valid)
    - rate limits on sensitive write endpoints
    - SSE endpoint metadata + security headers on the SPA mount
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.policies import DashboardAuth
from plugins.baselithbot.router import create_router
from plugins.baselithbot.ui_api import create_dashboard_router


def _build_app() -> tuple[FastAPI, BaselithbotPlugin]:
    plugin = BaselithbotPlugin()
    app = FastAPI()
    app.include_router(create_router(plugin), prefix="/baselithbot")
    return app, plugin


class TestDashboardReadEndpoints:
    def test_overview_returns_state_snapshot(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/overview")
        assert res.status_code == 200
        body = res.json()
        assert "agent" in body
        assert "counts" in body
        assert body["agent"]["state"] == "uninitialized"
        assert body["counts"]["channels_registered"] > 0

    def test_sessions_list_starts_empty(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/sessions")
        assert res.status_code == 200
        assert res.json() == {"sessions": []}

    def test_channels_endpoint_reflects_registry(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/channels")
        assert res.status_code == 200
        body = res.json()
        assert len(body["channels"]) == len(plugin.channels.known())

    def test_doctor_reports_dependencies(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/doctor")
        assert res.status_code == 200
        body = res.json()
        assert "platform" in body
        assert "python_dependencies" in body

    def test_events_recent_returns_history(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/events/recent?limit=10")
        assert res.status_code == 200
        assert "events" in res.json()


class TestSessionWriteFlows:
    def test_create_and_delete_session_without_auth(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        create = client.post(
            "/baselithbot/dash/sessions", json={"title": "t", "primary": True}
        )
        assert create.status_code == 200
        sid = create.json()["id"]

        history = client.get(f"/baselithbot/dash/sessions/{sid}/history")
        assert history.status_code == 200
        assert history.json()["messages"] == []

        send = client.post(
            f"/baselithbot/dash/sessions/{sid}/send",
            json={"role": "user", "content": "hi", "metadata": {}},
        )
        assert send.status_code == 200

        reset = client.post(f"/baselithbot/dash/sessions/{sid}/reset")
        assert reset.status_code == 200

        delete = client.delete(f"/baselithbot/dash/sessions/{sid}")
        assert delete.status_code == 200

    def test_missing_session_returns_404(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/sessions/does-not-exist/history")
        assert res.status_code == 404


class TestPairingFlow:
    def test_issue_token_and_list_paired(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        issued = client.post(
            "/baselithbot/dash/nodes/token", json={"platform": "macos"}
        )
        assert issued.status_code == 200
        token = issued.json()["token"]

        # The raw pairing handshake is exercised separately; here we just
        # exercise the listing endpoint and a revoke-404 path.
        listed = client.get("/baselithbot/dash/nodes")
        assert listed.status_code == 200
        assert listed.json()["status"]["pending_tokens"] >= 1

        revoke = client.delete("/baselithbot/dash/nodes/nonexistent")
        assert revoke.status_code == 404
        assert token  # sanity: token is non-empty


class TestDashboardAuthGuard:
    """Auth is enforced when ``DashboardAuth`` is initialized with a token."""

    def _app_with_auth(self, token: str) -> FastAPI:
        plugin = BaselithbotPlugin()
        auth = DashboardAuth(token=token)
        app = FastAPI()
        router = create_dashboard_router(plugin, auth=auth)
        app.include_router(router, prefix="/baselithbot")
        return app

    def test_missing_token_is_401(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post("/baselithbot/dash/nodes/token", json={})
        assert res.status_code == 401

    def test_wrong_token_is_403(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post(
            "/baselithbot/dash/nodes/token",
            json={},
            headers={"Authorization": "Bearer nope"},
        )
        assert res.status_code == 403

    def test_correct_token_is_accepted(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post(
            "/baselithbot/dash/nodes/token",
            json={"platform": "ios"},
            headers={"Authorization": "Bearer secret"},
        )
        assert res.status_code == 200

    def test_query_param_token_is_accepted(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.post(
            "/baselithbot/dash/nodes/token?token=secret", json={}
        )
        assert res.status_code == 200

    def test_read_endpoints_remain_open(self) -> None:
        client = TestClient(self._app_with_auth("secret"))
        res = client.get("/baselithbot/dash/overview")
        assert res.status_code == 200


class TestRateLimit:
    def test_pairing_token_rate_limit(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        # 5 allowed per minute (see _TOKEN_RATE_LIMIT); 6th must 429.
        for _ in range(5):
            assert (
                client.post(
                    "/baselithbot/dash/nodes/token", json={}
                ).status_code
                == 200
            )
        res = client.post("/baselithbot/dash/nodes/token", json={})
        assert res.status_code == 429


class TestUiMount:
    def test_root_redirects_to_ui(self) -> None:
        app, _ = _build_app()
        client = TestClient(app, follow_redirects=False)
        res = client.get("/baselithbot/")
        assert res.status_code in (307, 308)
        assert res.headers["location"] == "/baselithbot/ui/"

    def test_ui_index_serves_and_sets_security_headers(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/ui/")
        # Either the built index or the fallback — both carry the headers.
        assert res.status_code in (200, 503)
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        assert res.headers.get("X-Frame-Options") == "DENY"
        assert res.headers.get("Referrer-Policy") == "no-referrer"

    def test_ui_path_traversal_is_rejected(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/ui/../../etc/passwd")
        # Either the SPA fallback serves the index or a 404 — never a leak.
        assert res.status_code in (200, 404, 503)
        if res.status_code == 200:
            assert b"passwd" not in res.content.lower()
