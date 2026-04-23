"""Tests for /dash/computer-use, /dash/stealth, /dash/audit-log + RuntimeConfigStore."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.computer_use.config import ComputerUseConfig
from plugins.baselithbot.plugin import BaselithbotPlugin
from plugins.baselithbot.api.router import create_router
from plugins.baselithbot.config.runtime import RuntimeConfigStore
from plugins.baselithbot.models import StealthConfig


def _build_app() -> tuple[FastAPI, BaselithbotPlugin, str]:
    state_dir = tempfile.mkdtemp(prefix="baselithbot-runtime-cfg-tests-")
    plugin = BaselithbotPlugin(state_dir=state_dir)
    app = FastAPI()
    app.include_router(create_router(plugin), prefix="/baselithbot")
    return app, plugin, state_dir


class TestRuntimeConfigStore:
    def test_overlay_merges_on_top_of_base(self) -> None:
        store = RuntimeConfigStore(tempfile.mkdtemp())
        base_cu = ComputerUseConfig(enabled=False, allow_shell=False)
        merged = store.get_computer_use(base_cu)
        assert merged.enabled is False

        store.set_computer_use(
            ComputerUseConfig(enabled=True, allow_shell=True, allow_filesystem=True)
        )
        merged = store.get_computer_use(base_cu)
        assert merged.enabled is True
        assert merged.allow_shell is True
        assert merged.allow_filesystem is True

    def test_persists_across_instances(self) -> None:
        state_dir = tempfile.mkdtemp()
        store = RuntimeConfigStore(state_dir)
        store.set_stealth(
            StealthConfig(enabled=False, rotate_user_agent=False, mask_webdriver=False)
        )

        reloaded = RuntimeConfigStore(state_dir)
        merged = reloaded.get_stealth(StealthConfig())
        assert merged.enabled is False
        assert merged.rotate_user_agent is False


class TestComputerUseRoutes:
    def test_get_returns_default_config(self) -> None:
        app, _, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/computer-use")
        assert res.status_code == 200
        body = res.json()
        assert "current" in body
        assert body["current"]["enabled"] is False

    def test_put_persists_and_invalidates_agent(self) -> None:
        app, plugin, state_dir = _build_app()
        client = TestClient(app)
        payload = {
            "enabled": True,
            "allow_mouse": True,
            "allow_keyboard": True,
            "allow_screenshot": True,
            "allow_shell": False,
            "allow_filesystem": False,
            "allowed_shell_commands": ["ls", "pwd"],
            "shell_timeout_seconds": 15.0,
            "filesystem_root": None,
            "filesystem_max_bytes": 5_000_000,
            "audit_log_path": None,
        }
        res = client.put("/baselithbot/dash/computer-use", json=payload)
        assert res.status_code == 200
        assert res.json()["current"]["enabled"] is True

        persisted = json.loads((Path(state_dir) / "runtime_config.json").read_text())
        assert persisted["computer_use"]["enabled"] is True
        assert persisted["computer_use"]["allowed_shell_commands"] == ["ls", "pwd"]

        effective = plugin.effective_computer_use_config()
        assert effective.enabled is True
        assert effective.shell_timeout_seconds == 15.0


class TestStealthRoutes:
    def test_get_default(self) -> None:
        app, _, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/stealth")
        assert res.status_code == 200
        body = res.json()
        assert body["current"]["enabled"] is True

    def test_put_overrides(self) -> None:
        app, plugin, _ = _build_app()
        client = TestClient(app)
        payload = {
            "enabled": False,
            "rotate_user_agent": False,
            "mask_webdriver": False,
            "spoof_languages": ["it-IT"],
            "spoof_timezone": "Europe/Rome",
            "user_agents": ["test-agent/1.0"],
        }
        res = client.put("/baselithbot/dash/stealth", json=payload)
        assert res.status_code == 200
        body = res.json()
        assert body["current"]["spoof_timezone"] == "Europe/Rome"
        eff = plugin.effective_stealth_config()
        assert eff.enabled is False
        assert eff.spoof_languages == ["it-IT"]


class TestAuditLogRoute:
    def test_unconfigured_returns_empty(self) -> None:
        app, _, _ = _build_app()
        client = TestClient(app)
        res = client.get("/baselithbot/dash/audit-log")
        assert res.status_code == 200
        body = res.json()
        assert body["configured"] is False
        assert body["file_exists"] is False
        assert body["returned"] == 0
        assert body["entries"] == []

    def test_missing_file_reports_configured_but_absent(self) -> None:
        app, _, state_dir = _build_app()
        client = TestClient(app)
        missing_path = Path(state_dir) / "missing-audit.jsonl"
        client.put(
            "/baselithbot/dash/computer-use",
            json={
                "enabled": True,
                "allow_mouse": True,
                "allow_keyboard": True,
                "allow_screenshot": True,
                "allow_shell": False,
                "allow_filesystem": False,
                "allowed_shell_commands": [],
                "shell_timeout_seconds": 30.0,
                "filesystem_root": None,
                "filesystem_max_bytes": 10_000_000,
                "audit_log_path": str(missing_path),
            },
        )
        res = client.get("/baselithbot/dash/audit-log")
        assert res.status_code == 200
        body = res.json()
        assert body["configured"] is True
        assert body["file_exists"] is False
        assert body["path"] == str(missing_path)
        assert body["entries"] == []

    def test_returns_tail_with_filter(self) -> None:
        app, plugin, state_dir = _build_app()
        client = TestClient(app)
        log_path = Path(state_dir) / "audit.jsonl"
        log_path.write_text(
            "\n".join(
                json.dumps({"ts": float(i), "action": act, "status": "success"})
                for i, act in enumerate(
                    ["mouse_click", "shell_run", "fs_read", "shell_run"]
                )
            )
            + "\n"
        )
        client.put(
            "/baselithbot/dash/computer-use",
            json={
                "enabled": True,
                "allow_mouse": True,
                "allow_keyboard": True,
                "allow_screenshot": True,
                "allow_shell": False,
                "allow_filesystem": False,
                "allowed_shell_commands": [],
                "shell_timeout_seconds": 30.0,
                "filesystem_root": None,
                "filesystem_max_bytes": 10_000_000,
                "audit_log_path": str(log_path),
            },
        )
        res = client.get("/baselithbot/dash/audit-log?limit=10&action=shell")
        assert res.status_code == 200
        body = res.json()
        assert body["configured"] is True
        assert body["file_exists"] is True
        assert body["returned"] == 2
        assert body["scanned_rows"] == 4
        assert body["status_counts"] == {"success": 2}
        assert body["action_counts"] == {"shell_run": 2}
        assert body["oldest_ts"] == 1.0
        assert body["newest_ts"] == 3.0
        assert all(e["action"] == "shell_run" for e in body["entries"])
