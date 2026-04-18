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
        app, plugin = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/doctor")
        assert res.status_code == 200
        body = res.json()
        assert "platform" in body
        assert "python_dependencies" in body
        assert "system_binaries" in body
        assert body["platform"]["python"].startswith(
            f"{__import__('sys').version_info.major}."
        )

        assert "plugin_runtime" in body
        runtime = body["plugin_runtime"]
        assert runtime["agent"]["state"] == "uninitialized"
        assert runtime["cron"]["backend"] == plugin.cron.backend
        assert runtime["channels"]["known"] == len(plugin.channels.known())
        assert runtime["workspaces"]["count"] == len(plugin.workspaces.list())
        assert runtime["provider_keys"]["total"] >= 0
        assert "events_in_buffer" in runtime["usage"]

        assert "state_paths" in body
        paths = body["state_paths"]
        assert paths["state_dir"]["exists"] is True
        assert paths["state_dir"]["kind"] == "dir"
        assert paths["state_dir"]["writable"] is True
        assert paths["workspaces"]["exists"] is True

    def test_events_recent_returns_history(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/events/recent?limit=10")
        assert res.status_code == 200
        assert "events" in res.json()


class TestCanvasRoutes:
    def test_canvas_snapshot_initially_empty(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/canvas")
        assert res.status_code == 200
        body = res.json()
        assert body["widgets"] == []
        assert body["revision"] == 0

    def test_canvas_render_accepts_nested_and_extra_widgets(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        payload = {
            "clear": True,
            "widgets": [
                {
                    "type": "list",
                    "items": [
                        {"type": "text", "content": "nested"},
                        {"type": "progress", "value": 0.25},
                    ],
                },
                {
                    "type": "form",
                    "submit_action": "noop",
                    "fields": [{"name": "q", "type": "text"}],
                },
                {"type": "divider"},
            ],
        }
        res = client.post("/baselithbot/dash/canvas/render", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()
        widgets = body["snapshot"]["widgets"]
        assert widgets[0]["type"] == "list"
        assert widgets[0]["items"][0]["content"] == "nested"
        assert widgets[0]["items"][1]["type"] == "progress"
        assert widgets[1]["type"] == "form"
        assert widgets[2]["type"] == "divider"
        assert len(plugin.canvas.widgets) == 3

    def test_canvas_render_rejects_unknown_widget(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.post(
            "/baselithbot/dash/canvas/render",
            json={"widgets": [{"type": "ghost"}]},
        )
        assert res.status_code == 400

    def test_canvas_clear_resets_surface(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        client.post(
            "/baselithbot/dash/canvas/render",
            json={"widgets": [{"type": "text", "content": "hi"}]},
        )
        assert len(plugin.canvas.widgets) == 1
        res = client.post("/baselithbot/dash/canvas/clear")
        assert res.status_code == 200
        assert res.json()["snapshot"]["widgets"] == []
        assert len(plugin.canvas.widgets) == 0

    def test_canvas_dispatch_publishes_event(self) -> None:
        from plugins.baselithbot.dashboard.bus import get_event_bus

        app, _ = _build_app()
        client = TestClient(app)
        res = client.post(
            "/baselithbot/dash/canvas/dispatch",
            json={"widget_id": "btn-x", "action": "demo.ping", "payload": {"k": 1}},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "dispatched"
        assert body["action"] == "demo.ping"
        recent = get_event_bus().recent(limit=50)
        assert any(e["type"] == "canvas.action" for e in recent)


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


class TestCustomCronEndpoints:
    """POST /crons, PUT /crons/{name}/custom, catalog surface."""

    def test_catalog_lists_supported_actions(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/crons/catalog")
        assert res.status_code == 200
        body = res.json()
        types = {entry["type"] for entry in body["actions"]}
        assert {"log", "chat_command", "http_webhook"}.issubset(types)
        assert body["name_prefix"] == "custom."

    def test_create_log_cron_then_surface_in_listing(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        payload = {
            "name": "ping",
            "interval_seconds": 120,
            "description": "heartbeat",
            "enabled": True,
            "action": {"type": "log", "params": {"message": "tick"}},
        }
        res = client.post("/baselithbot/dash/crons", json=payload)
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "created"
        assert body["job"]["name"] == "custom.ping"

        assert plugin.custom_crons.get("custom.ping") is not None

        listed = client.get("/baselithbot/dash/crons").json()
        names = {job["name"]: job for job in listed["jobs"]}
        assert "custom.ping" in names
        assert names["custom.ping"]["custom"] is True

    def test_create_rejects_unknown_action(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.post(
            "/baselithbot/dash/crons",
            json={
                "name": "nope",
                "interval_seconds": 60,
                "action": {"type": "nuke", "params": {}},
            },
        )
        assert res.status_code == 400

    def test_create_duplicate_is_400(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.custom_crons.register(_build_custom_spec("dup", {"message": "x"}))
        res = client.post(
            "/baselithbot/dash/crons",
            json={
                "name": "dup",
                "interval_seconds": 60,
                "action": {"type": "log", "params": {"message": "y"}},
            },
        )
        assert res.status_code == 400

    def test_update_custom_cron_replaces_spec(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.custom_crons.register(_build_custom_spec("ping", {"message": "v1"}))

        res = client.put(
            "/baselithbot/dash/crons/custom.ping/custom",
            json={
                "interval_seconds": 300,
                "description": "updated",
                "enabled": False,
                "action": {"type": "log", "params": {"message": "v2"}},
            },
        )
        assert res.status_code == 200
        info = plugin.cron.get("custom.ping")
        assert info is not None
        assert info["interval_seconds"] == 300
        assert info["enabled"] is False

    def test_remove_custom_cron_clears_store(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.custom_crons.register(_build_custom_spec("ping", {"message": "v1"}))
        res = client.post("/baselithbot/dash/crons/custom.ping/remove")
        assert res.status_code == 200
        assert res.json()["custom"] is True
        assert plugin.custom_crons.get("custom.ping") is None
        assert plugin.cron.get("custom.ping") is None


def _build_custom_spec(name: str, params: dict):
    from plugins.baselithbot.cron_custom import CronActionSpec, CustomCronSpec

    return CustomCronSpec(
        name=name,
        interval_seconds=30,
        action=CronActionSpec(type="log", params=params),
    )


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

    def test_query_param_token_is_rejected(self) -> None:
        """Query-param ``?token=`` must be refused to prevent log/referer leaks."""
        client = TestClient(self._app_with_auth("secret"))
        res = client.post("/baselithbot/dash/nodes/token?token=secret", json={})
        assert res.status_code == 401

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


class TestWorkspacesCRUD:
    def test_default_workspace_is_auto_bootstrapped(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/workspaces")
        assert res.status_code == 200
        names = [w["name"] for w in res.json()["workspaces"]]
        assert "default" in names
        default = next(w for w in res.json()["workspaces"] if w["name"] == "default")
        assert default["primary"] is True
        assert "description" in default
        assert "metadata" in default

    def test_workspace_create_persists_and_lists(self) -> None:
        state_dir = tempfile.mkdtemp(prefix="baselithbot-ws-persist-")
        plugin = BaselithbotPlugin(state_dir=state_dir)
        app = FastAPI()
        app.include_router(create_router(plugin), prefix="/baselithbot")
        client = TestClient(app)

        payload = {
            "name": "sandbox",
            "description": "experimental bucket",
            "primary": False,
            "channel_overrides": {"slack": {"team": "T1"}},
            "metadata": {"owner": "ops"},
        }
        res = client.post("/baselithbot/dash/workspaces", json=payload)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["status"] == "created"
        assert body["workspace"]["name"] == "sandbox"
        assert body["workspace"]["description"] == "experimental bucket"
        assert body["workspace"]["channels_overridden"] == ["slack"]
        assert body["workspace"]["metadata"] == {"owner": "ops"}

        plugin2 = BaselithbotPlugin(state_dir=state_dir)
        names = {w.config.name for w in plugin2.workspaces.list()}
        assert "sandbox" in names
        assert "default" in names

    def test_workspace_create_conflict_on_duplicate_name(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        payload = {"name": "default"}
        res = client.post("/baselithbot/dash/workspaces", json=payload)
        assert res.status_code == 409

    def test_workspace_update_toggles_primary_and_demotes_old(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        client.post("/baselithbot/dash/workspaces", json={"name": "alt"})
        res = client.put(
            "/baselithbot/dash/workspaces/alt",
            json={
                "description": "now primary",
                "primary": True,
                "channel_overrides": {},
                "metadata": {},
            },
        )
        assert res.status_code == 200, res.text
        names_primary = {
            w.config.name: w.config.primary for w in plugin.workspaces.list()
        }
        assert names_primary["alt"] is True
        assert names_primary["default"] is False

    def test_workspace_delete_blocks_primary(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.delete("/baselithbot/dash/workspaces/default")
        assert res.status_code == 409

    def test_workspace_delete_blocks_last_workspace(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        for w in list(plugin.workspaces.list()):
            if w.config.name != "default":
                plugin.workspaces.remove(w.config.name)
        plugin.workspaces.get("default").config.primary = False
        res = client.delete("/baselithbot/dash/workspaces/default")
        assert res.status_code == 409

    def test_workspace_delete_removes_non_primary(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        client.post("/baselithbot/dash/workspaces", json={"name": "ephemeral"})
        res = client.delete("/baselithbot/dash/workspaces/ephemeral")
        assert res.status_code == 200
        res2 = client.get("/baselithbot/dash/workspaces")
        names = [w["name"] for w in res2.json()["workspaces"]]
        assert "ephemeral" not in names


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
