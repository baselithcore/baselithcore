"""Unit tests for the Baselithbot plugin — sessions, chat commands, workspaces, nodes."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.asyncio
async def test_session_manager_lifecycle() -> None:
    from plugins.baselithbot.sessions import SessionManager, SessionMessage

    mgr = SessionManager()
    s = mgr.create(title="t1")
    mgr.send(s.id, SessionMessage(role="user", content="hello"))
    history = mgr.history(s.id)
    assert history and history[0].content == "hello"
    mgr.reset(s.id)
    assert mgr.history(s.id) == []
    assert mgr.delete(s.id) is True


@pytest.mark.asyncio
async def test_chat_command_router_status_default() -> None:
    from plugins.baselithbot.chat.commands import (
        SUPPORTED_COMMANDS,
        ChatCommandRouter,
    )

    router = ChatCommandRouter()
    out = await router.handle("/status")
    assert out["command"] == "status"
    assert "uptime_seconds" in out
    assert set(SUPPORTED_COMMANDS) <= set(out["stats"].keys())

    unknown = await router.handle("/nope")
    assert unknown["status"] == "unknown"


@pytest.mark.asyncio
async def test_chat_command_router_custom_handler() -> None:
    from plugins.baselithbot.chat.commands import ChatCommandRouter

    router = ChatCommandRouter()

    async def think_handler(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del context
        return {"command": "think", "received": args}

    router.register("think", think_handler)
    out = await router.handle("/think one two")
    assert out == {"command": "think", "received": ["one", "two"]}


def test_node_pairing_round_trip() -> None:
    from plugins.baselithbot.nodes import NodePairing, PairingError

    p = NodePairing()
    token = p.issue_token(platform="ios")
    result = p.register_handshake(token, node_id="n-1", platform="ios")
    assert result.node_id == "n-1"
    assert {n.node_id for n in p.list_paired()} == {"n-1"}
    with pytest.raises(PairingError):
        p.register_handshake(token, node_id="n-1", platform="ios")


def test_workspace_manager_isolates_state() -> None:
    from plugins.baselithbot.workspace import WorkspaceConfig, WorkspaceManager

    mgr = WorkspaceManager()
    mgr.create(WorkspaceConfig(name="alpha"))
    mgr.create(WorkspaceConfig(name="beta"))
    assert {w.config.name for w in mgr.list()} == {"alpha", "beta"}
    assert mgr.sessions("alpha") is not mgr.sessions("beta")


@pytest.mark.asyncio
async def test_slash_default_handlers_wired() -> None:
    from plugins.baselithbot.chat.commands import ChatCommandRouter
    from plugins.baselithbot.chat.slash_defaults import install_default_handlers
    from plugins.baselithbot.observability.usage import UsageEvent, UsageLedger
    from plugins.baselithbot.sessions import SessionManager

    router = ChatCommandRouter()
    sessions = SessionManager()
    ledger = UsageLedger()
    state = install_default_handlers(router, sessions=sessions, usage=ledger)

    out_new = await router.handle("/new my-session")
    assert out_new["session"]["title"] == "my-session"

    ledger.record(UsageEvent(prompt_tokens=10, completion_tokens=20))
    out_usage = await router.handle("/usage")
    assert out_usage["total_tokens"] == 30

    out_verbose = await router.handle("/verbose on")
    assert out_verbose["enabled"] is True
    assert state.verbose is True

    await router.handle("/restart")
    assert state.restart_requested is True
