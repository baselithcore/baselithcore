"""Unit tests for the A2A discovery (well-known) router."""

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core.a2a.agent_card import AgentCapabilities, AgentCard  # noqa: E402
from core.a2a.router import create_wellknown_router  # noqa: E402


def _client() -> TestClient:
    card = AgentCard(
        name="test-agent",
        description="A test agent",
        version="9.9.9",
        agentCapabilities=AgentCapabilities(streaming=True),
    )
    app = FastAPI()
    app.include_router(create_wellknown_router(card))
    return TestClient(app)


def test_wellknown_endpoint_serves_card():
    resp = _client().get("/.well-known/agent.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "test-agent"
    assert body["version"] == "9.9.9"
    assert body["capabilities"]["streaming"] is True


def test_agent_card_alias():
    resp = _client().get("/a2a/agent-card")
    assert resp.status_code == 200
    assert resp.json()["name"] == "test-agent"
