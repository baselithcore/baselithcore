"""Unit tests for the Baselithbot dashboard channel-config and session flows.

Covers channel config partial updates / secret masking, start-stop-delete
lifecycle, and the session create/send/reset/delete write paths.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ._baselithbot_dashboard_helpers import _build_app


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
