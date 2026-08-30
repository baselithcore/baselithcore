"""Auth coverage for the non-dashboard Baselithbot endpoints.

``/status`` and ``/metrics`` used to be anonymous while every dashboard route
was bearer-gated: agent state, backend/stealth flags and run/channel/usage
telemetry were readable without a credential (core ``/metrics`` defaults to
admin auth). Both now go through the same fail-closed ``DashboardAuth``.
``/run`` errors used to echo the raw exception text into the 500 detail.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugins.baselithbot import BaselithbotPlugin

_TOKEN = "test-dashboard-token"


def _client(monkeypatch, *, with_token: bool) -> TestClient:
    if with_token:
        monkeypatch.setenv("BASELITHBOT_DASHBOARD_TOKEN", _TOKEN)
    else:
        monkeypatch.delenv("BASELITHBOT_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("BASELITHBOT_DASHBOARD_ALLOW_INSECURE", raising=False)
    plugin = BaselithbotPlugin()
    app = FastAPI()
    app.include_router(plugin.create_router(), prefix="/baselithbot")
    client = TestClient(app, raise_server_exceptions=False)
    client._plugin = plugin  # type: ignore[attr-defined]
    return client


def _bearer() -> dict[str, str]:
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_status_refuses_anonymous_callers(monkeypatch):
    client = _client(monkeypatch, with_token=False)
    assert client.get("/baselithbot/status").status_code == 503  # fail-closed


def test_metrics_refuses_anonymous_callers(monkeypatch):
    client = _client(monkeypatch, with_token=False)
    assert client.get("/baselithbot/metrics").status_code == 503  # fail-closed


def test_status_and_metrics_serve_authenticated_callers(monkeypatch):
    client = _client(monkeypatch, with_token=True)
    status = client.get("/baselithbot/status", headers=_bearer())
    assert status.status_code == 200
    assert status.json()["state"] == "uninitialized"
    metrics = client.get("/baselithbot/metrics", headers=_bearer())
    assert metrics.status_code == 200


def test_run_500_does_not_echo_internal_error(monkeypatch):
    client = _client(monkeypatch, with_token=True)
    plugin = client._plugin  # type: ignore[attr-defined]

    async def _boom():
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(plugin, "get_or_start_agent", _boom)

    response = client.post(
        "/baselithbot/run", json={"goal": "browse"}, headers=_bearer()
    )
    assert response.status_code == 500
    assert "secret-internal-detail" not in response.text


def test_ui_assets_carry_security_headers(monkeypatch, tmp_path):
    import plugins.baselithbot.api.router as router_module

    (tmp_path / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(router_module, "_UI_DIST", tmp_path)

    client = _client(monkeypatch, with_token=True)
    response = client.get("/baselithbot/ui/app.js")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"


@pytest.mark.parametrize("path", ["/baselithbot/status", "/baselithbot/metrics"])
def test_wrong_token_rejected(monkeypatch, path):
    client = _client(monkeypatch, with_token=True)
    response = client.get(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code in (401, 403)
