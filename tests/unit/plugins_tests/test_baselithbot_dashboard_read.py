"""Unit tests for the Baselithbot dashboard read paths, canvas and desktop tools.

Covers:
    - overview / sessions / channels / doctor / events read paths
    - canvas snapshot, render, clear and dispatch routes
    - desktop tool catalog + invocation routes
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from plugins.baselithbot.computer_use.config import ComputerUseConfig

from ._baselithbot_dashboard_helpers import _build_app


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


class TestDesktopRoutes:
    def test_desktop_tools_catalog_reflects_runtime_policy(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.runtime_config.set_computer_use(
            ComputerUseConfig(
                enabled=True,
                allow_shell=True,
                allow_filesystem=True,
                allowed_shell_commands=["pwd", "ls"],
                filesystem_root=tempfile.mkdtemp(prefix="baselithbot-desktop-root-"),
                require_approval_for=["shell", "filesystem"],
                approval_timeout_seconds=45.0,
            )
        )

        res = client.get("/baselithbot/dash/desktop/tools")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["policy"]["enabled"] is True
        assert body["policy"]["allow_shell"] is True
        assert body["policy"]["allow_filesystem"] is True
        assert body["policy"]["allowed_shell_commands"] == ["pwd", "ls"]
        assert body["policy"]["require_approval_for"] == ["shell", "filesystem"]
        assert body["policy"]["approval_timeout_seconds"] == 45.0
        names = {tool["name"] for tool in body["tools"]}
        assert "baselithbot_desktop_screenshot" in names
        assert "baselithbot_shell_run" in names
        assert "baselithbot_fs_list" in names

    def test_desktop_tool_invocation_uses_current_tool_map(self) -> None:
        root = tempfile.mkdtemp(prefix="baselithbot-desktop-fs-")
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.runtime_config.set_computer_use(
            ComputerUseConfig(
                enabled=True,
                allow_filesystem=True,
                filesystem_root=root,
            )
        )

        res = client.post(
            "/baselithbot/dash/desktop/tools/baselithbot_fs_list",
            json={"args": {"path": "."}},
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["tool"] == "baselithbot_fs_list"
        assert body["result"]["status"] == "success"
        assert body["result"]["path"] == str(Path(root).resolve())
        assert body["result"]["entries"] == []

    def test_desktop_tool_invocation_rejects_unknown_tool(self) -> None:
        app, _ = _build_app()
        client = TestClient(app)
        res = client.post(
            "/baselithbot/dash/desktop/tools/not-real",
            json={"args": {}},
        )
        assert res.status_code == 404

    def test_desktop_tool_invocation_rejects_bad_args(self) -> None:
        app, plugin = _build_app()
        client = TestClient(app)
        plugin.runtime_config.set_computer_use(
            ComputerUseConfig(
                enabled=True,
                allow_keyboard=True,
            )
        )

        res = client.post(
            "/baselithbot/dash/desktop/tools/baselithbot_kbd_press",
            json={"args": {}},
        )
        assert res.status_code == 422
        assert "invalid arguments" in res.text
