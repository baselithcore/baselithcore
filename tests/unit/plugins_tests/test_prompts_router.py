"""Tests for the prompt-catalog admin API (/prompts).

The operator half of durable prompt management: list the registry, register a
new version, promote a label — all write-through the synchronizer so every
replica converges. Without a configured synchronizer the write endpoints
refuse (503): a promotion that silently stays replica-local is a footgun.
"""

from __future__ import annotations

import plugins.api_routers.prompts as prompts_module
import pytest
from core.prompts.sync import PromptSynchronizer
from fastapi import FastAPI
from fastapi.testclient import TestClient
from plugins.api_routers.prompts import router

from core.prompts.registry import PromptRegistry
from plugins.api_routers.admin import verify_credentials
from tests.unit.core.prompts.test_prompt_sync import FakeBackend


@pytest.fixture
def registry():
    return PromptRegistry()


@pytest.fixture
def syncer(registry):
    return PromptSynchronizer(registry=registry, backend=FakeBackend())


def _client(monkeypatch, syncer, registry) -> TestClient:
    monkeypatch.setattr(prompts_module, "get_prompt_synchronizer", lambda: syncer)
    monkeypatch.setattr(prompts_module, "get_prompt_registry", lambda: registry)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    return TestClient(app)


def test_register_version_then_list(monkeypatch, syncer, registry):
    client = _client(monkeypatch, syncer, registry)

    response = client.post(
        "/prompts/greeting/versions",
        json={"version": "1", "template": "hello {{ name }}", "labels": ["production"]},
    )
    assert response.status_code == 201

    listing = client.get("/prompts")
    assert listing.status_code == 200
    body = listing.json()
    entry = next(p for p in body["prompts"] if p["name"] == "greeting")
    assert entry["versions"] == ["1"]
    assert entry["labels"] == {"production": "1"}


def test_promote_label(monkeypatch, syncer, registry):
    client = _client(monkeypatch, syncer, registry)
    client.post(
        "/prompts/greeting/versions", json={"version": "1", "template": "v1 {{ x }}"}
    )
    client.post(
        "/prompts/greeting/versions", json={"version": "2", "template": "v2 {{ x }}"}
    )

    response = client.post("/prompts/greeting/labels/production", json={"version": "2"})
    assert response.status_code == 200
    assert registry.get("greeting", label="production").version == "2"


def test_promote_unknown_version_is_404(monkeypatch, syncer, registry):
    client = _client(monkeypatch, syncer, registry)
    response = client.post("/prompts/ghost/labels/production", json={"version": "9"})
    assert response.status_code == 404


def test_writes_refuse_without_synchronizer(monkeypatch, registry):
    monkeypatch.setattr(prompts_module, "get_prompt_synchronizer", lambda: None)
    monkeypatch.setattr(prompts_module, "get_prompt_registry", lambda: registry)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_credentials] = lambda: "admin"
    client = TestClient(app)

    response = client.post(
        "/prompts/greeting/versions", json={"version": "1", "template": "x"}
    )
    assert response.status_code == 503

    # Reads still work replica-locally.
    assert client.get("/prompts").status_code == 200
