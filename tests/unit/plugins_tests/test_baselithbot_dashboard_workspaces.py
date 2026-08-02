"""Unit tests for the Baselithbot dashboard model preferences and workspaces CRUD."""

from __future__ import annotations

import tempfile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot.api.router import create_router
from plugins.baselithbot.plugin import BaselithbotPlugin

from ._baselithbot_dashboard_helpers import _build_app


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
