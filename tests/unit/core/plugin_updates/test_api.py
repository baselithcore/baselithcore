from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from baselith.core.middleware.security import require_admin
from baselith.core.plugin_updates import api as api_mod
from baselith.core.plugin_updates.models import CheckReport, UpdateCandidate
from baselith.core.plugins.api import router as plugin_management_router


def _report() -> CheckReport:
    return CheckReport(
        checked_at=datetime.now(UTC),
        candidates=[
            UpdateCandidate(
                plugin="demo", installed_version="1.0.0", latest=None, available=True
            )
        ],
    )


class _FakeService:
    def __init__(self) -> None:
        self.checks = 0

    def report(self) -> CheckReport | None:
        return _report()

    async def request_check(self) -> CheckReport:
        self.checks += 1
        return _report()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    # Same order as baselith/core/api/factory.py: updates first, or /{plugin_name} wins.
    app.include_router(api_mod.router)
    app.include_router(plugin_management_router)
    app.dependency_overrides[require_admin] = lambda: "admin"
    yield TestClient(app)


def test_get_without_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: None)
    res = client.get("/api/plugins/updates")
    assert res.status_code == 200
    assert res.json() == {"enabled": False, "report": None}


def test_get_with_service(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: _FakeService())
    body = client.get("/api/plugins/updates").json()
    assert body["enabled"] is True
    assert body["report"]["candidates"][0]["plugin"] == "demo"


def test_check_without_service_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: None)
    res = client.post("/api/plugins/updates/check")
    assert res.status_code == 503
    assert res.json()["detail"] == "plugin updates are not configured"


def test_check_runs_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeService()
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: fake)
    res = client.post("/api/plugins/updates/check")
    assert res.status_code == 200 and fake.checks == 1
    assert res.json()["candidates"][0]["plugin"] == "demo"


def test_updates_route_not_swallowed_by_plugin_detail(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: None)
    res = client.get("/api/plugins/updates")
    assert res.status_code == 200 and "enabled" in res.json()


def test_factory_registers_updates_before_plugin_management() -> None:
    import inspect

    from baselith.core.api import factory

    src = inspect.getsource(factory)
    # Both routers are mounted from one ordered list (served unprefixed and
    # under /v1); updates must come first or /{plugin_name} swallows /updates.
    assert src.index("plugin_api_routers = [plugin_updates_router]") < src.index(
        "plugin_api_routers.append(plugin_management_router)"
    )


class _PreflightService(_FakeService):
    def __init__(self) -> None:
        super().__init__()
        self.asked: list[tuple[str, str | None]] = []

    def preflight(self, subject: str, version: str | None = None) -> list[object]:
        from baselith.core.plugin_updates.preflight import PreflightItem

        self.asked.append((subject, version))
        return [PreflightItem(code="ready", level="ok", message="go")]


def test_preflight_route_serves_the_items(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _PreflightService()
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: svc)
    res = client.get(
        "/api/plugins/updates/preflight", params={"subject": "core", "version": "1.4.0"}
    )
    assert res.status_code == 200
    assert res.json() == [{"code": "ready", "level": "ok", "message": "go"}]
    assert svc.asked == [("core", "1.4.0")]


def test_preflight_route_without_service_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_mod, "get_plugin_update_service", lambda: None)
    res = client.get("/api/plugins/updates/preflight", params={"subject": "core"})
    assert res.status_code == 503


def test_preflight_route_needs_a_subject(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        api_mod, "get_plugin_update_service", lambda: _PreflightService()
    )
    assert client.get("/api/plugins/updates/preflight").status_code == 422


def test_preflight_route_requires_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(api_mod.router)
    monkeypatch.setattr(
        api_mod, "get_plugin_update_service", lambda: _PreflightService()
    )
    res = TestClient(app).get(
        "/api/plugins/updates/preflight", params={"subject": "core"}
    )
    assert res.status_code in (401, 403)


async def test_service_preflight_reads_the_live_inputs(tmp_path, monkeypatch) -> None:
    """The real service: a known candidate, no updater: blocks, never raises."""
    from baselith.core.config.plugin_updates import PluginUpdateConfig
    from baselith.core.plugin_updates import _preflight_live
    from baselith.core.plugin_updates import service as svc_mod

    def no_redis() -> int:
        raise ConnectionError("redis down")

    monkeypatch.setattr(_preflight_live, "queue_jobs", no_redis)
    svc = svc_mod.PluginUpdateService(
        PluginUpdateConfig(sources_file=None, cache_dir=tmp_path / "c"),
        bundled_root=tmp_path / "plugins",
    )
    svc._cache.save(
        _report().model_copy(
            update={
                "candidates": [
                    UpdateCandidate(
                        plugin="demo",
                        installed_version="1.0.0",
                        latest=_release("1.1.0"),
                        available=True,
                        trust="provenance",
                    )
                ]
            }
        )
    )
    codes = [i.code for i in svc.preflight("demo")]
    assert "blocker_updater_offline" in codes
    assert "blocker_unsigned_release" in codes
    assert "queue_probe_unavailable" in codes
    assert "no_boot_report" in codes
    assert "ready" not in codes


def _release(version: str):
    from baselith.core.plugin_updates.models import ReleaseInfo

    return ReleaseInfo(
        plugin="demo", version=version, tag=f"v{version}", published_at=None
    )
