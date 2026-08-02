"""Unit tests for the Baselithbot plugin — agent registry, routing, custom agents."""

from __future__ import annotations

from typing import Any

import pytest

from plugins.baselithbot import BaselithbotPlugin


@pytest.mark.asyncio
async def test_agent_router_picks_best_keyword_match() -> None:
    from plugins.baselithbot.agents import AgentEntry, AgentRegistry, AgentRouter

    reg = AgentRegistry()

    async def coder(query, context):
        return {"who": "coder", "q": query}

    async def writer(query, context):
        return {"who": "writer", "q": query}

    reg.register(
        AgentEntry(name="coder", keywords=["python", "bug", "code"], priority=200),
        coder,
    )
    reg.register(
        AgentEntry(name="writer", keywords=["essay", "article"], priority=100),
        writer,
    )

    router = AgentRouter(reg)
    decision = router.decide("fix python bug")
    assert decision.agent == "coder"
    out = await router.dispatch("write me an article")
    assert out["status"] == "dispatched"
    assert out["result"]["who"] == "writer"


def test_plugin_seeds_default_system_agents(tmp_path: Any) -> None:
    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    names = {entry.name for entry in plugin.agent_registry.list()}
    assert {"system.browse", "system.usage", "system.canvas"} <= names
    for name in ("system.browse", "system.usage", "system.canvas"):
        entry = plugin.agent_registry.get(name)
        assert entry is not None
        assert entry.metadata.get("kind") == "system"


@pytest.mark.asyncio
async def test_custom_agent_registry_registers_and_dispatches_static(
    tmp_path: Any,
) -> None:
    from plugins.baselithbot.agents import AgentActionSpec, CustomAgentSpec

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    spec = CustomAgentSpec(
        name="echo",
        description="echo",
        keywords=["echo"],
        priority=150,
        metadata={"owner": "test"},
        action=AgentActionSpec(
            type="static_response", params={"payload": {"ok": True}}
        ),
    )
    stored = plugin.custom_agents.register(spec)
    assert stored.name == "custom.echo"
    assert plugin.custom_agents.is_custom("custom.echo")
    entry = plugin.agent_registry.get("custom.echo")
    assert entry is not None
    assert entry.metadata.get("kind") == "custom"
    assert entry.metadata.get("action_type") == "static_response"
    result = await plugin.agent_registry.invoke("custom.echo", "hi", {})
    assert result["status"] == "success"
    assert result["result"] == {"ok": True}


def test_custom_agent_registry_rejects_bad_action(tmp_path: Any) -> None:
    from plugins.baselithbot.agents import AgentActionSpec, CustomAgentSpec

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    with pytest.raises(ValueError, match="unknown action type"):
        plugin.custom_agents.register(
            CustomAgentSpec(
                name="bogus",
                description="",
                keywords=[],
                priority=100,
                metadata={},
                action=AgentActionSpec(type="not_real", params={}),
            )
        )
    with pytest.raises(ValueError, match="starting with"):
        plugin.custom_agents.register(
            CustomAgentSpec(
                name="bad-cmd",
                description="",
                keywords=[],
                priority=100,
                metadata={},
                action=AgentActionSpec(
                    type="chat_command", params={"command": "no-slash"}
                ),
            )
        )


def test_custom_agents_persist_across_restart(tmp_path: Any) -> None:
    from plugins.baselithbot.agents import AgentActionSpec, CustomAgentSpec

    first = BaselithbotPlugin(state_dir=str(tmp_path))
    first.custom_agents.register(
        CustomAgentSpec(
            name="persisted",
            description="",
            keywords=["kw"],
            priority=100,
            metadata={},
            action=AgentActionSpec(
                type="static_response", params={"payload": {"survived": True}}
            ),
        )
    )
    assert (tmp_path / "custom_agents.json").is_file()

    second = BaselithbotPlugin(state_dir=str(tmp_path))
    loaded = second.custom_agents.bootstrap()
    assert loaded == 1
    entry = second.agent_registry.get("custom.persisted")
    assert entry is not None


def test_dashboard_agents_routes_end_to_end(tmp_path: Any) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins.baselithbot.dashboard.app import create_dashboard_router

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    app = FastAPI()
    app.include_router(create_dashboard_router(plugin))
    client = TestClient(app)

    resp = client.get("/dash/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["system"] == 3
    assert body["name_prefix"] == "custom."

    resp = client.get("/dash/agents/catalog")
    assert resp.status_code == 200
    types = {a["type"] for a in resp.json()["actions"]}
    assert {"chat_command", "http_webhook", "static_response"} <= types

    resp = client.post(
        "/dash/agents",
        json={
            "name": "pong",
            "description": "",
            "keywords": ["ping"],
            "priority": 100,
            "metadata": {},
            "action": {"type": "static_response", "params": {"payload": {"ok": 1}}},
        },
    )
    assert resp.status_code == 200, resp.text
    agent = resp.json()["agent"]
    assert agent["name"] == "custom.pong"
    assert agent["custom"] is True

    resp = client.post(
        "/dash/agents/custom.pong/dispatch",
        json={"query": "hi", "context": {}},
    )
    assert resp.status_code == 200
    inner = resp.json()["result"]
    assert inner["status"] == "success"
    assert inner["result"] == {"ok": 1}

    resp = client.delete("/dash/agents/system.browse")
    assert resp.status_code == 409

    resp = client.delete("/dash/agents/custom.pong")
    assert resp.status_code == 200
