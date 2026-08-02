"""Unit tests for the Baselithbot dashboard cron REST surface.

Covers the built-in cron routes (list, toggle, run-now, interval, remove)
plus the custom-cron endpoints (catalog, create, update, remove).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from ._baselithbot_dashboard_helpers import _build_app


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
    from plugins.baselithbot.cron.custom import CronActionSpec, CustomCronSpec

    return CustomCronSpec(
        name=name,
        interval_seconds=30,
        action=CronActionSpec(type="log", params=params),
    )
