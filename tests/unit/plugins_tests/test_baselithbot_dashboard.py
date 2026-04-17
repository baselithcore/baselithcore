"""Unit tests for the Baselithbot dashboard API + auth + security headers.

Covers:
    - overview / sessions / crons / nodes / doctor read paths
    - bearer-token guard (missing / wrong / valid)
    - rate limits on sensitive write endpoints
    - SSE endpoint metadata + security headers on the SPA mount
"""

from __future__ import annotations

import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.policies import DashboardAuth
from plugins.baselithbot.router import create_router
from plugins.baselithbot.ui_api import create_dashboard_router


def _build_app() -> tuple[FastAPI, BaselithbotPlugin]:
    plugin = BaselithbotPlugin(
        state_dir=tempfile.mkdtemp(prefix="baselithbot-dashboard-tests-")
    )
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


class TestChannelConfigFlows:
    def test_partial_channel_update_preserves_masked_secret(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)

        initial = client.put(
            "/baselithbot/dash/channels/matrix/config",
            json={
                "config": {
                    "homeserver": "https://matrix.example",
                    "access_token": "super-secret-token",
                    "room_id": "!ops:example.org",
                }
            },
        )
        assert initial.status_code == 200

        detail = client.get("/baselithbot/dash/channels/matrix/config")
        assert detail.status_code == 200
        assert detail.json()["safe_config"]["access_token"] == "***oken"

        update = client.put(
            "/baselithbot/dash/channels/matrix/config",
            json={
                "config": {"homeserver": "https://matrix-2.example"},
                "unset_fields": ["room_id"],
            },
        )
        assert update.status_code == 200

        stored = plugin.channel_configs.get_config("matrix")
        assert stored == {
            "homeserver": "https://matrix-2.example",
            "access_token": "super-secret-token",
        }

    def test_channel_start_stop_and_delete_flow(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)

        saved = client.put(
            "/baselithbot/dash/channels/slack/config",
            json={"config": {"webhook_url": "http://127.0.0.1:9999/hooks/test"}},
        )
        assert saved.status_code == 200

        started = client.post("/baselithbot/dash/channels/slack/start")
        assert started.status_code == 200
        assert started.json()["adapter_status"] == "ready"
        assert plugin.channel_configs.is_enabled("slack") is True

        listing = client.get("/baselithbot/dash/channels")
        assert listing.status_code == 200
        slack = next(c for c in listing.json()["channels"] if c["name"] == "slack")
        assert slack["live"] is True
        assert slack["configured"] is True

        stopped = client.post("/baselithbot/dash/channels/slack/stop")
        assert stopped.status_code == 200
        assert plugin.channel_configs.is_enabled("slack") is False

        deleted = client.delete("/baselithbot/dash/channels/slack/config")
        assert deleted.status_code == 200
        assert plugin.channel_configs.has("slack") is False


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
            json={"role": "user", "content": "/status", "metadata": {}},
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


class TestCronDashboardFlow:
    """Exercise the full cron REST surface: list, toggle, run-now, interval, remove."""

    @staticmethod
    async def _noop() -> None:
        return None

    def test_list_returns_registered_jobs(self) -> None:
        app, plugin = _build_app()
        plugin.cron.add_interval("unit.job", self._noop, seconds=30, description="unit")
        client = TestClient(app)
        res = client.get("/baselithbot/dash/crons")
        assert res.status_code == 200
        body = res.json()
        assert body["backend"] == "interval"
        names = {job["name"] for job in body["jobs"]}
        assert "unit.job" in names

    def test_toggle_endpoint_flips_enabled_flag(self) -> None:
        app, plugin = _build_app()
        plugin.cron.add_interval("unit.job", self._noop, seconds=30)
        client = TestClient(app)

        pause = client.post(
            "/baselithbot/dash/crons/unit.job/toggle", json={"enabled": False}
        )
        assert pause.status_code == 200
        assert pause.json()["job"]["enabled"] is False

        resume = client.post(
            "/baselithbot/dash/crons/unit.job/toggle", json={"enabled": True}
        )
        assert resume.status_code == 200
        assert resume.json()["job"]["enabled"] is True

    def test_toggle_missing_is_404(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.post(
            "/baselithbot/dash/crons/ghost/toggle", json={"enabled": False}
        )
        assert res.status_code == 404

    def test_run_now_triggers_job(self) -> None:
        app, plugin = _build_app()
        plugin.cron.add_interval("unit.job", self._noop, seconds=3600)
        client = TestClient(app)
        res = client.post("/baselithbot/dash/crons/unit.job/run")
        assert res.status_code == 200
        assert res.json()["status"] == "triggered"
        info = plugin.cron.get("unit.job")
        assert info is not None
        import time as _time

        assert float(info["next_run_at"]) <= _time.time() + 0.01  # type: ignore[arg-type]

    def test_update_interval_persists_value(self) -> None:
        app, plugin = _build_app()
        plugin.cron.add_interval("unit.job", self._noop, seconds=30)
        client = TestClient(app)

        res = client.patch(
            "/baselithbot/dash/crons/unit.job", json={"interval_seconds": 7}
        )
        assert res.status_code == 200
        assert res.json()["job"]["interval_seconds"] == 7
        info = plugin.cron.get("unit.job")
        assert info is not None and info["interval_seconds"] == 7

    def test_update_interval_rejects_zero(self) -> None:
        app, plugin = _build_app()
        plugin.cron.add_interval("unit.job", self._noop, seconds=30)
        client = TestClient(app)
        res = client.patch(
            "/baselithbot/dash/crons/unit.job", json={"interval_seconds": 0}
        )
        assert res.status_code == 422  # pydantic ge=1 validation

    def test_remove_job_endpoint(self) -> None:
        app, plugin = _build_app()
        plugin.cron.add_interval("unit.job", self._noop, seconds=30)
        client = TestClient(app)

        res = client.post("/baselithbot/dash/crons/unit.job/remove")
        assert res.status_code == 200
        assert plugin.cron.get("unit.job") is None

        # Second removal surfaces 404.
        res404 = client.post("/baselithbot/dash/crons/unit.job/remove")
        assert res404.status_code == 404


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
        plugin = BaselithbotPlugin(
            state_dir=tempfile.mkdtemp(prefix="baselithbot-dashboard-tests-")
        )
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
        res = client.post("/baselithbot/dash/nodes/token?token=secret", json={})
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
                client.post("/baselithbot/dash/nodes/token", json={}).status_code == 200
            )
        res = client.post("/baselithbot/dash/nodes/token", json={})
        assert res.status_code == 429


class TestModelPreferences:
    def test_get_returns_current_and_catalog(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/models")
        assert res.status_code == 200
        body = res.json()
        assert "current" in body
        assert body["current"]["provider"] == "ollama"
        assert "openai" in body["options"]["llm_providers"]
        assert "google" in body["options"]["vision_providers"]

    def test_put_updates_and_persists(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        payload = {
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "temperature": 0.2,
            "max_tokens": 4096,
            "vision_provider": "anthropic",
            "vision_model": "claude-3-5-sonnet-20241022",
            "failover_chain": [
                {
                    "provider": "openai",
                    "model": "gpt-4o",
                    "cooldown_seconds": 15.0,
                }
            ],
        }
        res = client.put("/baselithbot/dash/models", json=payload)
        assert res.status_code == 200
        body = res.json()
        assert body["current"]["provider"] == "anthropic"
        assert body["current"]["failover_chain"][0]["model"] == "gpt-4o"
        # Preference store returns the new state on subsequent reads.
        assert plugin.model_preferences.get().model == "claude-opus-4-7"

    def test_put_rejects_unknown_provider(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.put(
            "/baselithbot/dash/models",
            json={
                "provider": "haxx",
                "model": "pwned",
                "temperature": 0.5,
                "vision_provider": "openai",
                "vision_model": "gpt-4o",
            },
        )
        assert res.status_code == 422


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
