"""Every API router is served under ``/v1``; its unprefixed copy is deprecated.

Only six routers used to have a ``/v1`` alias; compliance, approvals, runs,
webhooks, privacy, prompts, the async-agent API and the plugin-management API
existed at their unprefixed path only, so a client could not pin the version
of most of the surface. The unprefixed copies stay live (existing clients keep
working) but are marked deprecated in OpenAPI and announced on the wire with
RFC 9745 ``Deprecation`` + an RFC 5829 ``successor-version`` link. Operational
paths (health probes, metrics, status, admin) are neither versioned away nor
deprecated.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from core.api.versioning import DEPRECATION_HEADER_VALUE, versioned_routers
from core.middleware.api_deprecation import APIDeprecationMiddleware

_REPO_ROOT = str(pathlib.Path(__file__).resolve().parents[4])


def _demo_app() -> FastAPI:
    router = APIRouter(prefix="/things")

    @router.get("/{thing_id}")
    async def get_thing(thing_id: str) -> dict[str, str]:
        if thing_id == "missing":
            raise HTTPException(status_code=404, detail="no such thing")
        return {"id": thing_id}

    app = FastAPI()
    for wrapped in versioned_routers([router]):
        app.include_router(wrapped)
    app.add_middleware(APIDeprecationMiddleware)
    return app


class TestVersionedRouters:
    def test_both_paths_serve_and_only_the_unprefixed_one_is_deprecated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("API_V1_ENABLED", raising=False)
        client = TestClient(_demo_app())

        legacy = client.get("/things/a")
        assert legacy.status_code == 200
        assert legacy.headers["deprecation"] == DEPRECATION_HEADER_VALUE
        assert legacy.headers["link"] == '</v1/things/a>; rel="successor-version"'

        current = client.get("/v1/things/a")
        assert current.status_code == 200
        assert "deprecation" not in current.headers
        assert "link" not in current.headers

        paths = client.get("/openapi.json").json()["paths"]
        assert paths["/things/{thing_id}"]["get"]["deprecated"] is True
        assert "deprecated" not in paths["/v1/things/{thing_id}"]["get"]

    def test_successor_link_keeps_the_root_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Behind a path-prefixing proxy the successor must stay reachable."""
        monkeypatch.delenv("API_V1_ENABLED", raising=False)
        client = TestClient(_demo_app(), root_path="/api")

        legacy = client.get("/things/a")
        assert legacy.status_code == 200
        assert legacy.headers["link"] == '</api/v1/things/a>; rel="successor-version"'

    def test_error_responses_of_a_deprecated_route_carry_the_headers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("API_V1_ENABLED", raising=False)
        resp = TestClient(_demo_app()).get("/things/missing")
        assert resp.status_code == 404
        assert resp.headers["deprecation"] == DEPRECATION_HEADER_VALUE

    def test_unmatched_paths_are_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("API_V1_ENABLED", raising=False)
        resp = TestClient(_demo_app()).get("/nowhere")
        assert resp.status_code == 404
        assert "deprecation" not in resp.headers

    def test_v1_disabled_returns_routers_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("API_V1_ENABLED", "false")
        router = APIRouter(prefix="/x")
        assert versioned_routers([router]) == [router]


class TestApiRoutersPlugin:
    def test_every_api_router_is_mounted_under_v1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        import core.config.compliance as compliance_cfg
        import core.config.orchestration as orchestration_cfg
        import core.config.privacy as privacy_cfg
        import core.config.webhooks as webhooks_cfg
        from plugins.api_routers.plugin import ApiRoutersPlugin

        on = SimpleNamespace(enabled=True, checkpoint_enabled=True)
        monkeypatch.setattr(webhooks_cfg, "get_webhook_config", lambda: on)
        monkeypatch.setattr(privacy_cfg, "get_privacy_config", lambda: on)
        monkeypatch.setattr(compliance_cfg, "get_compliance_config", lambda: on)
        monkeypatch.setattr(orchestration_cfg, "get_orchestration_config", lambda: on)
        monkeypatch.delenv("API_V1_ENABLED", raising=False)

        app = FastAPI()
        for router in ApiRoutersPlugin().get_routers():
            app.include_router(router)
        paths = app.openapi()["paths"]
        for prefix in (
            "/compliance",
            "/approvals",
            "/runs",
            "/webhooks",
            "/privacy",
            "/prompts",
            "/agent",
        ):
            legacy = [p for p in paths if p.startswith(prefix)]
            assert legacy, prefix
            for path in legacy:
                assert f"/v1{path}" in paths, path
                for op in paths[path].values():
                    assert op.get("deprecated") is True, path
                for op in paths[f"/v1{path}"].values():
                    assert not op.get("deprecated"), path


_CHILD = r"""
import json
from fastapi.testclient import TestClient
from core.api.factory import create_app

app = create_app()
paths = app.openapi()["paths"]
client = TestClient(app, raise_server_exceptions=False)
health = client.get("/health")
legacy_chat = client.post("/chat", json={"query": "x"})
v1_chat = client.post("/v1/chat", json={"query": "x"})
print("===BEGIN===")
print(json.dumps({
    "paths": {p: [bool(op.get("deprecated")) for op in ops.values()] for p, ops in paths.items()},
    "health_deprecation": health.headers.get("deprecation"),
    "legacy_chat": [legacy_chat.status_code, legacy_chat.headers.get("deprecation"), legacy_chat.headers.get("link")],
    "v1_chat": [v1_chat.status_code, v1_chat.headers.get("deprecation")],
}))
print("===END===")
"""


@pytest.fixture(scope="module")
def factory_paths() -> dict[str, Any]:
    env = os.environ.copy()
    env.pop("API_V1_ENABLED", None)
    env.update(
        {
            "ENABLE_FEEDBACK": "true",
            "TRUSTED_HOSTS": '["testserver"]',
            "PYTHONPATH": _REPO_ROOT + os.pathsep + env.get("PYTHONPATH", ""),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD],
        capture_output=True,
        text=True,
        env=env,
        cwd=_REPO_ROOT,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    payload = proc.stdout.split("===BEGIN===")[1].split("===END===")[0]
    result: dict[str, Any] = json.loads(payload.strip())
    return result


class TestFactory:
    def test_core_api_routers_are_versioned_and_deprecated_at_the_root(
        self, factory_paths: dict[str, Any]
    ) -> None:
        paths = factory_paths["paths"]
        for path in ("/chat", "/chat/stream", "/reindex", "/feedback", "/api/plugins/"):
            assert all(paths[path]), path
            assert f"/v1{path}" in paths, path
            assert not any(paths[f"/v1{path}"]), path

    def test_operational_paths_are_not_deprecated(
        self, factory_paths: dict[str, Any]
    ) -> None:
        paths = factory_paths["paths"]
        for path in ("/health", "/health/ready", "/metrics", "/status"):
            if path in paths:
                assert not any(paths[path]), path
        assert factory_paths["health_deprecation"] is None

    def test_a_refused_legacy_request_still_announces_the_successor(
        self, factory_paths: dict[str, Any]
    ) -> None:
        status, deprecation, link = factory_paths["legacy_chat"]
        # Whatever the outcome (auth refusal or success), the deprecated path
        # says so, and names its /v1 successor.
        assert deprecation == DEPRECATION_HEADER_VALUE, status
        assert link == '</v1/chat>; rel="successor-version"'
        v1_status, v1_deprecation = factory_paths["v1_chat"]
        assert v1_status == status
        assert v1_deprecation is None
