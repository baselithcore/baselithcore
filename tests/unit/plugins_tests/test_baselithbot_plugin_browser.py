"""Unit tests for the Baselithbot plugin — browser agent, stealth, HTTP pool."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.lifecycle.protocols import AgentState
from plugins.baselithbot import (
    BaselithbotAgent,
    BaselithbotPlugin,
    BaselithbotResult,
    BaselithbotTask,
)
from plugins.baselithbot.browser.js_whitelist import ALLOWED_SNIPPETS
from plugins.baselithbot.browser.tools import build_baselithbot_tool_definitions
from plugins.browser_agent.types import (
    BrowserAction,
    BrowserActionType,
    PageState,
)


def _fake_page_state(url: str = "https://example.com") -> PageState:
    return PageState(
        url=url,
        title="Example",
        screenshot_base64="ZmFrZQ==",
        viewport_width=1280,
        viewport_height=720,
        visible_text="hello",
    )


def _make_backend_mock() -> MagicMock:
    backend = MagicMock()
    backend.start = AsyncMock()
    backend.stop = AsyncMock()
    backend.navigate = AsyncMock(return_value=_fake_page_state())
    backend.get_page_state = AsyncMock(return_value=_fake_page_state())
    backend.execute_action = AsyncMock(return_value=True)
    backend.click = AsyncMock(return_value=True)
    backend.type_text = AsyncMock(return_value=True)
    backend.screenshot = AsyncMock(return_value="ZmFrZQ==")
    backend._context = MagicMock()
    backend._context.set_extra_http_headers = AsyncMock()
    backend._context.add_init_script = AsyncMock()
    backend._page = MagicMock()
    backend._page.url = "https://example.com"
    backend._page.evaluate = AsyncMock(return_value=42)
    return backend


def _make_prestarted_agent(backend: MagicMock) -> BaselithbotAgent:
    """Build a BaselithbotAgent with backend pre-injected and state forced READY."""
    agent = BaselithbotAgent(config={"stealth": {"enabled": False}})
    agent._backend = backend
    agent._state = AgentState.READY
    return agent


@pytest.mark.asyncio
async def test_agent_startup_transitions_to_ready() -> None:
    backend = _make_backend_mock()
    with patch("plugins.baselithbot.browser.agent.BrowserAgent", return_value=backend):
        agent = BaselithbotAgent(
            config={"headless": True, "stealth": {"enabled": False}}
        )
        assert agent.state == AgentState.UNINITIALIZED
        await agent.startup()
        assert agent.state == AgentState.READY
        backend.start.assert_awaited_once()
        await agent.shutdown()
        assert agent.state == AgentState.STOPPED
        backend.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_execute_returns_failure_when_not_ready() -> None:
    agent = BaselithbotAgent(config={"stealth": {"enabled": False}})
    result = await agent.execute("just browse")
    assert isinstance(result, BaselithbotResult)
    assert result.success is False
    assert "not ready" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_agent_execute_completes_on_done_action() -> None:
    backend = _make_backend_mock()
    backend.decide_next_action = AsyncMock(
        return_value=BrowserAction(
            action_type=BrowserActionType.DONE,
            reasoning="goal reached",
        )
    )
    with patch("plugins.baselithbot.browser.agent.BrowserAgent", return_value=backend):
        agent = BaselithbotAgent(config={"stealth": {"enabled": False}})
        await agent.startup()
        result = await agent.execute(
            BaselithbotTask(goal="open homepage", start_url="https://example.com")
        )
        assert result.success is True
        assert result.steps_taken == 1
        assert result.final_url == "https://example.com"
        backend.navigate.assert_awaited()
        await agent.shutdown()


@pytest.mark.asyncio
async def test_agent_execute_records_extraction() -> None:
    backend = _make_backend_mock()
    actions = iter(
        [
            BrowserAction(
                action_type=BrowserActionType.EXTRACT,
                value="title,price",
                reasoning="extract product",
            ),
            BrowserAction(
                action_type=BrowserActionType.DONE,
                reasoning="done",
            ),
        ]
    )
    backend.decide_next_action = AsyncMock(side_effect=lambda *a, **k: next(actions))
    with patch("plugins.baselithbot.browser.agent.BrowserAgent", return_value=backend):
        agent = BaselithbotAgent(config={"stealth": {"enabled": False}})
        await agent.startup()
        result = await agent.execute(
            BaselithbotTask(goal="get product", extract_fields=["title", "price"])
        )
        assert result.success is True
        assert "title" in result.extracted_data
        assert "price" in result.extracted_data
        await agent.shutdown()


@pytest.mark.asyncio
async def test_eval_js_safe_rejects_unknown_snippet() -> None:
    backend = _make_backend_mock()
    tools = build_baselithbot_tool_definitions(
        agent_factory=lambda: _make_prestarted_agent(backend)
    )
    eval_tool = next(t for t in tools if t["name"] == "baselithbot_eval_js_safe")
    out = await eval_tool["handler"]("rm -rf /", {})
    assert out["status"] == "error"
    assert "whitelist" in out["error"]


@pytest.mark.asyncio
async def test_eval_js_safe_executes_whitelisted_snippet() -> None:
    backend = _make_backend_mock()
    tools = build_baselithbot_tool_definitions(
        agent_factory=lambda: _make_prestarted_agent(backend)
    )
    eval_tool = next(t for t in tools if t["name"] == "baselithbot_eval_js_safe")
    snippet_id = "scroll_by"
    assert snippet_id in ALLOWED_SNIPPETS
    out = await eval_tool["handler"](snippet_id, {"pixels": 500})
    assert out["status"] == "success", out
    assert out["snippet_id"] == snippet_id
    backend._page.evaluate.assert_awaited()


@pytest.mark.asyncio
async def test_flow_handler_handle_browse_returns_orchestrator_envelope() -> None:
    from plugins.baselithbot.api.handlers import BaselithbotFlowHandler

    backend = _make_backend_mock()
    backend.decide_next_action = AsyncMock(
        return_value=BrowserAction(
            action_type=BrowserActionType.DONE,
            reasoning="goal reached",
        )
    )
    plugin = BaselithbotPlugin()
    await plugin.initialize({"stealth": {"enabled": False}})
    plugin._agent = _make_prestarted_agent(backend)

    handler = BaselithbotFlowHandler(plugin)
    out = await handler.handle_browse(
        "search baselithcore", {"start_url": "https://example.com"}
    )
    assert out["status"] == "success"
    assert "Completed" in out["response"]
    assert out["data"]["final_url"] == "https://example.com"


@pytest.mark.asyncio
async def test_http_pool_reuses_client() -> None:
    from plugins.baselithbot.browser.http_pool import HTTPClientPool

    pool = HTTPClientPool()
    try:
        c1 = await pool.acquire(timeout=5.0)
        c2 = await pool.acquire(timeout=5.0)
        assert c1 is c2
        c3 = await pool.acquire(timeout=10.0)
        assert c3 is not c1
    finally:
        await pool.close_all()


@pytest.mark.asyncio
async def test_http_pool_blocks_internal_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: clients issued by HTTPClientPool must reject internal targets.

    ``http_pool.py`` backs the custom agent/cron "webhook" action, whose
    target URL comes straight from user-supplied config — the most severe
    SSRF vector in the plugin. ``test_http_pool_reuses_client`` above only
    covers instance caching; this asserts the pooled client is actually
    SSRF-hardened (DNS mocked to an internal IP, no real network I/O).
    """
    import socket

    from core.security.ssrf import SsrfError
    from plugins.baselithbot.browser.http_pool import HTTPClientPool

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.delenv("BASELITHBOT_ALLOW_INTERNAL_WEBHOOKS", raising=False)

    pool = HTTPClientPool()
    try:
        client = await pool.acquire(timeout=5.0)
        with pytest.raises(SsrfError):
            await client.get("https://internal.corp.example/hook")
    finally:
        await pool.close_all()


def test_stealth_pick_user_agent_uses_secrets() -> None:
    import inspect

    from plugins.baselithbot.browser.stealth import pick_user_agent
    from plugins.baselithbot.models import StealthConfig

    src = inspect.getsource(pick_user_agent)
    assert "secrets.choice" in src
    cfg = StealthConfig()
    assert pick_user_agent(cfg) in cfg.user_agents


def test_stealth_context_options_apply_timezone_locale_and_deterministic_ua() -> None:
    from plugins.baselithbot.browser.stealth import build_browser_context_options
    from plugins.baselithbot.models import StealthConfig

    cfg = StealthConfig(
        enabled=True,
        rotate_user_agent=False,
        spoof_languages=["it-IT", "it"],
        spoof_timezone="Europe/Rome",
        user_agents=["ua-fixed", "ua-spare"],
    )
    assert build_browser_context_options(cfg) == {
        "user_agent": "ua-fixed",
        "locale": "it-IT",
        "timezone_id": "Europe/Rome",
    }


@pytest.mark.asyncio
async def test_agent_startup_passes_stealth_context_options_to_browser_backend() -> (
    None
):
    backend = _make_backend_mock()
    with patch(
        "plugins.baselithbot.browser.agent.BrowserAgent", return_value=backend
    ) as browser_cls:
        agent = BaselithbotAgent(
            config={
                "stealth": {
                    "enabled": True,
                    "rotate_user_agent": False,
                    "spoof_languages": ["it-IT", "it"],
                    "spoof_timezone": "Europe/Rome",
                    "user_agents": ["ua-fixed", "ua-spare"],
                }
            }
        )
        await agent.startup()
        kwargs = browser_cls.call_args.kwargs
        assert kwargs["context_options"] == {
            "user_agent": "ua-fixed",
            "locale": "it-IT",
            "timezone_id": "Europe/Rome",
        }
        await agent.shutdown()
