"""Unit tests for the Baselithbot plugin."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.lifecycle.protocols import AgentState
from plugins.browser_agent.types import (
    BrowserAction,
    BrowserActionType,
    PageState,
)
from plugins.baselithbot import (
    BaselithbotAgent,
    BaselithbotPlugin,
    BaselithbotResult,
    BaselithbotTask,
    StealthConfig,
)
from plugins.baselithbot.js_whitelist import ALLOWED_SNIPPETS
from plugins.baselithbot.tools import build_baselithbot_tool_definitions


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


@pytest.mark.asyncio
async def test_agent_startup_transitions_to_ready() -> None:
    backend = _make_backend_mock()
    with patch("plugins.baselithbot.agent.BrowserAgent", return_value=backend):
        agent = BaselithbotAgent(config={"headless": True, "stealth": {"enabled": False}})
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
    with patch("plugins.baselithbot.agent.BrowserAgent", return_value=backend):
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
    with patch("plugins.baselithbot.agent.BrowserAgent", return_value=backend):
        agent = BaselithbotAgent(config={"stealth": {"enabled": False}})
        await agent.startup()
        result = await agent.execute(
            BaselithbotTask(goal="get product", extract_fields=["title", "price"])
        )
        assert result.success is True
        assert "title" in result.extracted_data
        assert "price" in result.extracted_data
        await agent.shutdown()


def test_plugin_exposes_manifest_and_intents() -> None:
    plugin = BaselithbotPlugin()
    intents = plugin.get_intent_patterns()
    assert any(intent["name"] == "baselithbot_browse" for intent in intents)
    tools = plugin.get_mcp_tools()
    tool_names = {tool["name"] for tool in tools}
    assert {
        "baselithbot_navigate",
        "baselithbot_click",
        "baselithbot_type",
        "baselithbot_scroll",
        "baselithbot_screenshot",
        "baselithbot_eval_js_safe",
        "baselithbot_run_task",
    } <= tool_names


@pytest.mark.asyncio
async def test_plugin_initialize_parses_stealth_config() -> None:
    plugin = BaselithbotPlugin()
    await plugin.initialize(
        {
            "headless": False,
            "max_steps": 5,
            "stealth": {"enabled": True, "rotate_user_agent": False},
        }
    )
    assert isinstance(plugin._agent_config["stealth"], StealthConfig)
    assert plugin._agent_config["stealth"].rotate_user_agent is False


def _make_prestarted_agent(backend: MagicMock) -> BaselithbotAgent:
    """Build a BaselithbotAgent with backend pre-injected and state forced READY."""
    agent = BaselithbotAgent(config={"stealth": {"enabled": False}})
    agent._backend = backend
    agent._state = AgentState.READY
    return agent


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
    from plugins.baselithbot.handlers import BaselithbotFlowHandler

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


def test_plugin_get_flow_handlers_binds_intent() -> None:
    plugin = BaselithbotPlugin()
    handlers = plugin.get_flow_handlers()
    assert "baselithbot_browse" in handlers
    assert callable(handlers["baselithbot_browse"])


def test_cli_register_parser_adds_subcommand() -> None:
    import argparse

    from plugins.baselithbot.cli import register_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register_parser(subparsers, argparse.HelpFormatter)
    args = parser.parse_args(["baselithbot", "run", "open hn"])
    assert args.cmd == "baselithbot"
    assert args.baselithbot_cmd == "run"
    assert args.goal == "open hn"


# ---------------------------------------------------------------------------
# Computer Use layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_computer_use_disabled_by_default_returns_denied() -> None:
    from plugins.baselithbot.computer_tools import build_computer_tool_definitions
    from plugins.baselithbot.computer_use import ComputerUseConfig

    tools = build_computer_tool_definitions(ComputerUseConfig())
    by_name = {t["name"]: t for t in tools}

    out = await by_name["baselithbot_mouse_click"]["handler"](x=10, y=20)
    assert out["status"] == "denied"
    assert "disabled" in out["error"].lower()


@pytest.mark.asyncio
async def test_computer_use_capability_flag_blocks_when_off() -> None:
    from plugins.baselithbot.computer_tools import build_computer_tool_definitions
    from plugins.baselithbot.computer_use import ComputerUseConfig

    cfg = ComputerUseConfig(enabled=True, allow_mouse=False, allow_keyboard=True)
    tools = build_computer_tool_definitions(cfg)
    by_name = {t["name"]: t for t in tools}

    out = await by_name["baselithbot_mouse_click"]["handler"](x=10, y=20)
    assert out["status"] == "denied"
    assert "mouse" in out["error"].lower()


@pytest.mark.asyncio
async def test_shell_executor_blocks_unallowlisted_command(tmp_path) -> None:
    from plugins.baselithbot.computer_use import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.shell_exec import ShellExecutor

    cfg = ComputerUseConfig(
        enabled=True, allow_shell=True, allowed_shell_commands=["echo"]
    )
    audit = AuditLogger(str(tmp_path / "audit.log"))
    sh = ShellExecutor(cfg, audit)

    with pytest.raises(ComputerUseError, match="not in the allowlist"):
        await sh.run("rm -rf /tmp/x")


@pytest.mark.asyncio
async def test_shell_executor_runs_allowlisted_echo(tmp_path) -> None:
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.shell_exec import ShellExecutor

    cfg = ComputerUseConfig(
        enabled=True, allow_shell=True, allowed_shell_commands=["echo"]
    )
    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path))
    sh = ShellExecutor(cfg, audit)

    result = await sh.run("echo baselithbot")
    assert result["return_code"] == 0
    assert "baselithbot" in result["stdout"]
    assert audit_path.is_file()
    assert audit_path.read_text().count("\n") >= 1


@pytest.mark.asyncio
async def test_filesystem_blocks_path_escape(tmp_path) -> None:
    from plugins.baselithbot.computer_use import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    with pytest.raises(ComputerUseError, match="escapes filesystem_root"):
        await fs.read("../etc/passwd")


@pytest.mark.asyncio
async def test_filesystem_round_trip(tmp_path) -> None:
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    write_out = await fs.write("notes/hello.txt", "ciao baselithbot")
    assert write_out["bytes_written"] == len(b"ciao baselithbot")

    read_out = await fs.read("notes/hello.txt")
    assert read_out["content"] == "ciao baselithbot"

    listing = await fs.list_dir("notes")
    names = {entry["name"] for entry in listing["entries"]}
    assert "hello.txt" in names


@pytest.mark.asyncio
async def test_os_controller_audit_records_actions(tmp_path) -> None:
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.os_control import OSController

    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path))
    cfg = ComputerUseConfig(enabled=True, allow_mouse=True, allow_keyboard=True)
    ctrl = OSController(cfg, audit)

    fake_pyautogui = MagicMock()
    fake_pyautogui.click = MagicMock()
    fake_pyautogui.typewrite = MagicMock()
    fake_pyautogui.hotkey = MagicMock()

    with patch(
        "plugins.baselithbot.os_control._load_pyautogui",
        return_value=fake_pyautogui,
    ):
        await ctrl.mouse_click(x=10, y=20, button="left", clicks=1)
        await ctrl.kbd_type("ciao", interval=0.0)
        await ctrl.kbd_hotkey("ctrl", "c")

    fake_pyautogui.click.assert_called_once()
    fake_pyautogui.typewrite.assert_called_once_with("ciao", 0.0)
    fake_pyautogui.hotkey.assert_called_once_with("ctrl", "c")

    log_lines = audit_path.read_text().strip().splitlines()
    actions = [json.loads(ln)["action"] for ln in log_lines]
    assert actions == ["mouse_click", "kbd_type", "kbd_hotkey"]


def test_plugin_get_mcp_tools_includes_computer_use() -> None:
    plugin = BaselithbotPlugin()
    tools = plugin.get_mcp_tools()
    names = {t["name"] for t in tools}
    assert {
        "baselithbot_desktop_screenshot",
        "baselithbot_screen_size",
        "baselithbot_mouse_move",
        "baselithbot_mouse_click",
        "baselithbot_mouse_scroll",
        "baselithbot_kbd_type",
        "baselithbot_kbd_press",
        "baselithbot_kbd_hotkey",
        "baselithbot_shell_run",
        "baselithbot_fs_read",
        "baselithbot_fs_write",
        "baselithbot_fs_list",
    } <= names
