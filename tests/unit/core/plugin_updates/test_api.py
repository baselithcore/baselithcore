from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.middleware.security import require_admin
from core.plugin_updates import api as api_mod
from core.plugin_updates.models import CheckReport, UpdateCandidate
from core.plugins.api import router as plugin_management_router


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
    # Same order as core/api/factory.py: updates first, or /{plugin_name} wins.
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

    from core.api import factory

    src = inspect.getsource(factory)
    # Both routers are mounted from one ordered list (served unprefixed and
    # under /v1); updates must come first or /{plugin_name} swallows /updates.
    assert src.index("plugin_api_routers = [plugin_updates_router]") < src.index(
        "plugin_api_routers.append(plugin_management_router)"
    )
