"""Tests for the /metrics router: auth toggle and multiprocess registry."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import core.config.security as security_config_module


def _build_app() -> FastAPI:
    from plugins.api_routers import metrics as metrics_module

    app = FastAPI()
    app.include_router(metrics_module.router)
    return app


@pytest.fixture
def fresh_security_config(monkeypatch):
    """Force SecurityConfig re-read from env for each test."""
    monkeypatch.setattr(security_config_module, "_security_config", None)
    yield
    monkeypatch.setattr(security_config_module, "_security_config", None)


def test_metrics_requires_auth_by_default(monkeypatch, fresh_security_config):
    monkeypatch.delenv("METRICS_AUTH_REQUIRED", raising=False)
    client = TestClient(_build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 401


def test_metrics_public_when_auth_disabled(monkeypatch, fresh_security_config):
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    client = TestClient(_build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"# HELP" in resp.content or b"# TYPE" in resp.content


def test_metrics_uses_multiprocess_registry(
    monkeypatch, tmp_path, fresh_security_config
):
    monkeypatch.setenv("METRICS_AUTH_REQUIRED", "false")
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))

    calls: dict[str, object] = {}
    from prometheus_client import multiprocess

    original = multiprocess.MultiProcessCollector

    class SpyCollector(original):  # type: ignore[misc,valid-type]
        def __init__(self, registry, path=None):
            calls["registry"] = registry
            super().__init__(registry, path=path or str(tmp_path))

    monkeypatch.setattr(multiprocess, "MultiProcessCollector", SpyCollector)
    client = TestClient(_build_app())
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "registry" in calls
