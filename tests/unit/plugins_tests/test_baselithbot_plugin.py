"""Unit tests for the Baselithbot plugin."""

from __future__ import annotations

import asyncio
import json
from typing import Any
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
    audit.flush()
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

    audit.flush()
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


# ---------------------------------------------------------------------------
# OpenClaw parity layer
# ---------------------------------------------------------------------------


def test_plugin_get_mcp_tools_includes_openclaw_surface() -> None:
    plugin = BaselithbotPlugin()
    names = {t["name"] for t in plugin.get_mcp_tools()}
    assert {
        "baselithbot_channel_list",
        "baselithbot_channel_send",
        "baselithbot_session_create",
        "baselithbot_session_list",
        "baselithbot_session_history",
        "baselithbot_session_send",
        "baselithbot_session_reset",
        "baselithbot_chat_command",
        "baselithbot_doctor",
        "baselithbot_skills_list",
        "baselithbot_skills_inject",
        "baselithbot_voice_tts",
        "baselithbot_canvas_render",
        "baselithbot_cron_list",
        "baselithbot_tailscale_status",
        "baselithbot_node_pairing_token",
        "baselithbot_paired_nodes",
    } <= names


def test_default_channel_registry_lists_24_openclaw_channels() -> None:
    from plugins.baselithbot.channels import build_default_registry

    registry = build_default_registry()
    known = registry.known()
    assert len(known) == 24
    for required in ("whatsapp", "telegram", "slack", "discord", "webchat"):
        assert required in known


@pytest.mark.asyncio
async def test_webchat_adapter_round_trip() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.webchat import WebChatAdapter

    adapter = WebChatAdapter()
    await adapter.startup()
    out = await adapter.send(
        ChannelMessage(channel="webchat", target="user-1", text="hi")
    )
    assert out["status"] == "success"
    history = await adapter.history()
    assert history[-1]["text"] == "hi"


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
    from plugins.baselithbot.chat_commands import (
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
    from plugins.baselithbot.chat_commands import ChatCommandRouter

    router = ChatCommandRouter()

    async def think_handler(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del context
        return {"command": "think", "received": args}

    router.register("think", think_handler)
    out = await router.handle("/think one two")
    assert out == {"command": "think", "received": ["one", "two"]}


def test_skill_registry_scopes() -> None:
    from plugins.baselithbot.skills import Skill, SkillRegistry, SkillScope

    reg = SkillRegistry()
    reg.register(Skill(name="a", scope=SkillScope.BUNDLED))
    reg.register(Skill(name="b", scope=SkillScope.WORKSPACE))
    bundled = reg.list(scope=SkillScope.BUNDLED)
    assert {s.name for s in bundled} == {"a"}


def test_load_injection_bundle(tmp_path) -> None:
    from plugins.baselithbot.skills import load_injection_bundle

    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "SOUL.md").write_text("# soul")
    bundle = load_injection_bundle(tmp_path)
    assert bundle.agents_md and "agents" in bundle.agents_md
    assert bundle.soul_md and "soul" in bundle.soul_md
    assert bundle.tools_md is None
    block = bundle.to_prompt_block()
    assert "<soul>" in block and "<agents>" in block


def test_discover_local_skill_specs(tmp_path) -> None:
    from plugins.baselithbot.skills import discover_local_skill_specs

    skill_dir = tmp_path / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Demo Skill\ndescription: Local demo\n---\n\n# Demo\n"
    )
    (skill_dir / "MANIFEST.yaml").write_text(
        """
bundle: demo-skill
bundle_version: 1.0.0
compatibility:
  designed_for:
    surfaces:
      - cli
  tested_on:
    - platform: Baselithbot
      model: local
      surface: cli
      status: pass
      date: 2026-04-17
""".strip()
    )

    specs = discover_local_skill_specs(tmp_path)
    assert len(specs) == 1
    assert specs[0].name == "Demo Skill"
    assert specs[0].validation.status == "verified"


def test_bundled_skills_cover_core_capabilities() -> None:
    from plugins.baselithbot.skills import SkillScope, bundled_skills

    names = {s.name for s in bundled_skills()}
    assert {
        "baselithbot.browser",
        "baselithbot.computer_use",
        "baselithbot.shell",
        "baselithbot.canvas",
        "baselithbot.channels",
    }.issubset(names)
    assert all(s.scope == SkillScope.BUNDLED for s in bundled_skills())


def test_plugin_bootstrap_registers_bundled_skills(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope, bundled_skills

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    registered = {s.name for s in plugin.skills.list(SkillScope.BUNDLED)}
    assert registered == {s.name for s in bundled_skills()}


def test_plugin_bootstrap_scans_workspace_markdown(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    (tmp_path / "AGENTS.md").write_text("# agents")
    (tmp_path / "TOOLS.md").write_text("# tools")

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    workspace_skills = plugin.skills.list(SkillScope.WORKSPACE)
    assert len(workspace_skills) == 1
    skill = workspace_skills[0]
    assert "AGENTS.md" in skill.metadata.get("sources", {})
    assert "TOOLS.md" in skill.metadata.get("sources", {})


def test_plugin_rescan_workspace_skills_picks_up_new_files(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    assert plugin.skills.list(SkillScope.WORKSPACE) == []

    (tmp_path / "SOUL.md").write_text("# soul")
    removed = plugin.rescan_workspace_skills()
    assert removed == 0
    assert len(plugin.skills.list(SkillScope.WORKSPACE)) == 1


def test_plugin_bootstrap_registers_local_custom_skill(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    skill_dir = tmp_path / "skills" / "local-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: Local Demo\ndescription: Custom local skill\n---\n\n# Demo\n"
    )
    (skill_dir / "MANIFEST.yaml").write_text(
        """
bundle: local-demo
bundle_version: 1.0.0
compatibility:
  designed_for:
    surfaces:
      - cli
      - chat
  tested_on:
    - platform: Baselithbot
      model: local
      surface: cli
      status: pass
      date: 2026-04-17
""".strip()
    )

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    workspace_skills = plugin.skills.list(SkillScope.WORKSPACE)
    custom = next(
        skill
        for skill in workspace_skills
        if skill.metadata.get("kind") == "custom_skill"
    )
    assert custom.name == f"workspace.{tmp_path.name}.local-demo"
    assert custom.metadata["validation"]["status"] == "verified"


def test_plugin_reports_invalid_local_custom_skill(tmp_path) -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import SkillScope

    skill_dir = tmp_path / "skills" / "broken"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# missing frontmatter")

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    assert plugin.skills.list(SkillScope.WORKSPACE) == []
    reports = plugin.workspace_skill_reports()
    assert len(reports) == 1
    assert reports[0]["validation"]["status"] == "invalid"


def test_skills_dashboard_routes_full_lifecycle(tmp_path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugins.baselithbot.dashboard.app import create_dashboard_router
    from plugins.baselithbot.plugin import BaselithbotPlugin
    from plugins.baselithbot.skills import Skill, SkillScope

    plugin = BaselithbotPlugin(state_dir=str(tmp_path))
    plugin.skills.register(
        Skill(name="managed.test", scope=SkillScope.MANAGED, version="1.0.0")
    )

    app = FastAPI()
    app.include_router(create_dashboard_router(plugin))
    client = TestClient(app)

    res = client.get("/dash/skills")
    assert res.status_code == 200
    names = {s["name"] for s in res.json()["skills"]}
    assert "baselithbot.browser" in names
    assert "managed.test" in names

    res = client.get("/dash/skills?scope=managed")
    assert {s["name"] for s in res.json()["skills"]} == {"managed.test"}

    res = client.get("/dash/skills/workspace/validate")
    assert res.status_code == 200
    assert "counts" in res.json()

    res = client.get("/dash/skills/clawhub")
    body = res.json()
    assert res.status_code == 200
    assert body["base_url"]
    assert body["install_dir"]

    res = client.delete("/dash/skills/baselithbot.browser")
    assert res.status_code == 409

    res = client.delete("/dash/skills/managed.test")
    assert res.status_code == 200
    assert res.json()["status"] == "removed"
    assert plugin.skills.get("managed.test") is None

    res = client.delete("/dash/skills/does.not.exist")
    assert res.status_code == 404

    res = client.post("/dash/skills/rescan")
    assert res.status_code == 200
    assert "workspace_skills" in res.json()


def test_node_pairing_round_trip() -> None:
    from plugins.baselithbot.nodes import NodePairing, PairingError

    p = NodePairing()
    token = p.issue_token(platform="ios")
    result = p.register_handshake(token, node_id="n-1", platform="ios")
    assert result.node_id == "n-1"
    assert {n.node_id for n in p.list_paired()} == {"n-1"}
    with pytest.raises(PairingError):
        p.register_handshake(token, node_id="n-1", platform="ios")


def test_canvas_render_a2ui_envelope() -> None:
    from plugins.baselithbot.canvas import A2UIRenderer, CanvasSurface, CanvasText

    surface = CanvasSurface()
    surface.add(CanvasText(content="hello"))
    msg = A2UIRenderer().render(surface)
    assert msg.protocol == "a2ui"
    assert msg.surface_id == surface.surface_id
    assert msg.widgets and msg.widgets[0]["type"] == "text"


def test_canvas_builders_parse_nested_list() -> None:
    from plugins.baselithbot.canvas import CanvasList, build_widget

    parsed = build_widget(
        {
            "type": "list",
            "ordered": True,
            "items": [
                {"type": "text", "content": "hi"},
                {
                    "type": "list",
                    "items": [{"type": "text", "content": "nested"}],
                },
            ],
        }
    )
    assert isinstance(parsed, CanvasList)
    assert parsed.ordered is True
    assert len(parsed.items) == 2
    inner = parsed.items[1]
    assert isinstance(inner, CanvasList)
    assert inner.items[0].content == "nested"  # type: ignore[union-attr]


def test_canvas_builders_handle_all_widget_types() -> None:
    from plugins.baselithbot.canvas import build_widgets

    widgets = build_widgets(
        [
            {"type": "text", "content": "hi"},
            {"type": "button", "label": "Go", "action": "ping"},
            {"type": "image", "url": "https://example.com/a.png"},
            {
                "type": "form",
                "submit_action": "submit",
                "fields": [{"name": "x", "label": "X", "type": "text"}],
            },
            {"type": "table", "columns": ["a"], "rows": [[1], [2]]},
            {"type": "chart", "chart_type": "bar", "series": []},
            {"type": "progress", "value": 0.5},
            {"type": "divider"},
        ]
    )
    assert [w.type for w in widgets] == [
        "text",
        "button",
        "image",
        "form",
        "table",
        "chart",
        "progress",
        "divider",
    ]


def test_canvas_builders_reject_unknown_type() -> None:
    import pytest as _pytest

    from plugins.baselithbot.canvas import CanvasWidgetError, build_widget

    with _pytest.raises(CanvasWidgetError):
        build_widget({"type": "does-not-exist"})


def test_canvas_render_tool_supports_extras_and_nested_lists() -> None:
    import asyncio as _asyncio

    from plugins.baselithbot.canvas import CanvasSurface
    from plugins.baselithbot.openclaw_tools import build_openclaw_tool_definitions

    surface = CanvasSurface()
    tools = build_openclaw_tool_definitions(canvas=surface)
    handler = next(
        t["handler"] for t in tools if t["name"] == "baselithbot_canvas_render"
    )

    result = _asyncio.run(
        handler(
            widgets=[
                {
                    "type": "list",
                    "items": [
                        {"type": "text", "content": "inner"},
                        {"type": "progress", "value": 0.3},
                    ],
                },
                {"type": "divider"},
            ],
            clear=True,
        )
    )
    assert result["status"] == "success"
    rendered = result["a2ui"]["widgets"]
    assert rendered[0]["type"] == "list"
    assert len(rendered[0]["items"]) == 2
    assert rendered[0]["items"][1]["type"] == "progress"
    assert rendered[1]["type"] == "divider"


def test_canvas_render_tool_rejects_bad_widget() -> None:
    import asyncio as _asyncio

    from plugins.baselithbot.canvas import CanvasSurface
    from plugins.baselithbot.openclaw_tools import build_openclaw_tool_definitions

    tools = build_openclaw_tool_definitions(canvas=CanvasSurface())
    handler = next(
        t["handler"] for t in tools if t["name"] == "baselithbot_canvas_render"
    )

    result = _asyncio.run(handler(widgets=[{"type": "nope"}]))
    assert result["status"] == "error"
    assert result["tool"] == "canvas_render"


@pytest.mark.asyncio
async def test_cron_scheduler_runs_job() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    sched.add_interval("tick", tick, seconds=1)
    sched._jobs["tick"].next_run_at = 0.0
    await sched.start()
    await asyncio.sleep(1.5)
    await sched.stop()
    assert counter["n"] >= 1


@pytest.mark.asyncio
async def test_cron_scheduler_pause_resume_blocks_execution() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    sched.add_interval("tick", tick, seconds=1, description="unit test")
    sched._jobs["tick"].next_run_at = 0.0
    assert sched.set_enabled("tick", False) is True
    await sched.start()
    await asyncio.sleep(0.5)
    assert counter["n"] == 0
    assert sched.set_enabled("tick", True) is True
    sched._jobs["tick"].next_run_at = 0.0
    await asyncio.sleep(0.7)
    await sched.stop()
    assert counter["n"] >= 1


@pytest.mark.asyncio
async def test_cron_scheduler_trigger_runs_immediately() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()
    counter = {"n": 0}

    async def tick():
        counter["n"] += 1

    sched.add_interval("tick", tick, seconds=3600)
    await sched.start()
    await asyncio.sleep(0.05)
    assert counter["n"] == 0
    assert sched.trigger("tick") is True
    await asyncio.sleep(0.5)
    await sched.stop()
    assert counter["n"] >= 1


@pytest.mark.asyncio
async def test_cron_scheduler_set_interval_updates_schedule() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()

    async def noop():
        return None

    sched.add_interval("job", noop, seconds=10)
    assert sched.set_interval("job", 2) is True
    info = sched.get("job")
    assert info is not None
    assert info["interval_seconds"] == 2
    assert sched.set_interval("missing", 5) is False
    with pytest.raises(ValueError):
        sched.set_interval("job", 0)


def test_cron_scheduler_get_returns_description_and_none_for_missing() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()

    async def noop():
        return None

    sched.add_interval("job", noop, seconds=5, description="hello")
    info = sched.get("job")
    assert info is not None
    assert info["description"] == "hello"
    assert info["enabled"] is True
    assert sched.get("missing") is None


def test_custom_cron_store_persists_specs(tmp_path) -> None:
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronSpec,
        CustomCronStore,
    )

    store = CustomCronStore(tmp_path / "custom_crons.json")
    assert store.load() == []
    specs = [
        CustomCronSpec(
            name="custom.ping",
            interval_seconds=30,
            action=CronActionSpec(type="log", params={"message": "hi"}),
        )
    ]
    store.save(specs)
    reloaded = store.load()
    assert len(reloaded) == 1
    assert reloaded[0].name == "custom.ping"
    assert reloaded[0].action.type == "log"


def test_custom_cron_registry_register_auto_prefixes_and_persists(tmp_path) -> None:
    from plugins.baselithbot.cron import CronScheduler
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )

    sched = CronScheduler()
    store = CustomCronStore(tmp_path / "custom.json")
    registry = CustomCronRegistry(scheduler=sched, store=store)

    spec = CustomCronSpec(
        name="ping",  # no prefix -> registry should add it
        interval_seconds=60,
        action=CronActionSpec(type="log", params={"message": "ok"}),
    )
    stored = registry.register(spec)
    assert stored.name == "custom.ping"
    assert sched.get("custom.ping") is not None

    # Same name collides.
    with pytest.raises(ValueError):
        registry.register(spec)

    # Persistence round-trip.
    registry2 = CustomCronRegistry(scheduler=CronScheduler(), store=store)
    loaded = registry2.bootstrap()
    assert loaded == 1
    assert registry2.get("custom.ping") is not None


def test_custom_cron_registry_rejects_unknown_action(tmp_path) -> None:
    from plugins.baselithbot.cron import CronScheduler
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )

    registry = CustomCronRegistry(
        scheduler=CronScheduler(),
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    spec = CustomCronSpec(
        name="x",
        interval_seconds=30,
        action=CronActionSpec(type="nuclear_launch", params={}),
    )
    with pytest.raises(ValueError):
        registry.register(spec)


def test_custom_cron_registry_chat_command_requires_slash(tmp_path) -> None:
    from plugins.baselithbot.cron import CronScheduler
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )

    registry = CustomCronRegistry(
        scheduler=CronScheduler(),
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    bad = CustomCronSpec(
        name="cc",
        interval_seconds=30,
        action=CronActionSpec(type="chat_command", params={"command": "status"}),
    )
    with pytest.raises(ValueError):
        registry.register(bad)


@pytest.mark.asyncio
async def test_custom_cron_log_action_executes(tmp_path) -> None:
    from plugins.baselithbot.cron import CronScheduler
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )

    sched = CronScheduler()
    registry = CustomCronRegistry(
        scheduler=sched,
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    registry.register(
        CustomCronSpec(
            name="ping",
            interval_seconds=1,
            action=CronActionSpec(
                type="log",
                params={"message": "tick", "level": "info"},
            ),
        )
    )
    sched._jobs["custom.ping"].next_run_at = 0.0
    await sched.start()
    await asyncio.sleep(0.3)
    info = sched.get("custom.ping")
    await sched.stop()
    assert info is not None
    assert int(info["runs"]) >= 1  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_custom_cron_chat_command_dispatches(tmp_path) -> None:
    from plugins.baselithbot.chat_commands import ChatCommandRouter
    from plugins.baselithbot.cron import CronScheduler
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )

    router = ChatCommandRouter()
    sched = CronScheduler()
    registry = CustomCronRegistry(
        scheduler=sched,
        store=CustomCronStore(tmp_path / "custom.json"),
        chat_commands=router,
    )
    registry.register(
        CustomCronSpec(
            name="status-heartbeat",
            interval_seconds=1,
            action=CronActionSpec(
                type="chat_command",
                params={"command": "/status"},
            ),
        )
    )
    sched._jobs["custom.status-heartbeat"].next_run_at = 0.0
    await sched.start()
    await asyncio.sleep(0.3)
    await sched.stop()
    assert router._stats["status"] >= 1


def test_custom_cron_registry_update_and_delete(tmp_path) -> None:
    from plugins.baselithbot.cron import CronScheduler
    from plugins.baselithbot.cron_custom import (
        CronActionSpec,
        CustomCronRegistry,
        CustomCronSpec,
        CustomCronStore,
    )

    sched = CronScheduler()
    registry = CustomCronRegistry(
        scheduler=sched,
        store=CustomCronStore(tmp_path / "custom.json"),
    )
    registry.register(
        CustomCronSpec(
            name="job",
            interval_seconds=30,
            action=CronActionSpec(type="log", params={"message": "a"}),
        )
    )
    registry.update(
        "custom.job",
        CustomCronSpec(
            name="custom.job",
            interval_seconds=45,
            action=CronActionSpec(type="log", params={"message": "b"}),
            enabled=False,
        ),
    )
    info = sched.get("custom.job")
    assert info is not None
    assert info["interval_seconds"] == 45
    assert info["enabled"] is False
    assert registry.delete("custom.job") is True
    assert sched.get("custom.job") is None
    assert registry.delete("custom.job") is False


@pytest.mark.asyncio
async def test_baselithbot_plugin_initialize_starts_cron_and_defaults() -> None:
    from plugins.baselithbot.plugin import BaselithbotPlugin

    plugin = BaselithbotPlugin()
    try:
        await plugin.initialize({})
        names = {job["name"] for job in plugin.cron.list()}
        assert {
            "pairing.prune_tokens",
            "sessions.prune_inactive",
            "workspace.rescan_skills",
            "usage.heartbeat",
        }.issubset(names)
        assert plugin.cron.running is True
    finally:
        await plugin.shutdown()
    assert plugin.cron.running is False


@pytest.mark.asyncio
async def test_doctor_returns_environment_report() -> None:
    from plugins.baselithbot.doctor import run_doctor

    report = await run_doctor()
    assert "platform" in report
    assert "python_dependencies" in report
    assert "system_binaries" in report


# ---------------------------------------------------------------------------
# Code editing layer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_search_replace_literal(tmp_path) -> None:
    from plugins.baselithbot.code_edit import (
        SearchReplaceEdit,
        apply_search_replace,
    )
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.txt").write_text("hello world")
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    out = await apply_search_replace(
        SearchReplaceEdit(path="f.txt", pattern="world", replacement="baselithbot"),
        fs,
    )
    assert out["matches"] == 1
    assert (root / "f.txt").read_text() == "hello baselithbot"


@pytest.mark.asyncio
async def test_code_multi_file_write_atomic_rollback(tmp_path) -> None:
    from plugins.baselithbot.code_edit import MultiFileEdit, MultiFileEditor
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.txt").write_text("AA")
    (root / "b.txt").write_text("BB")

    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    editor = MultiFileEditor(fs)

    out = await editor.apply(
        [
            MultiFileEdit(path="a.txt", content="A2"),
            MultiFileEdit(path="b.txt", content="B2"),
        ]
    )
    assert out["status"] == "success"
    assert (root / "a.txt").read_text() == "A2"
    assert (root / "b.txt").read_text() == "B2"


@pytest.mark.asyncio
async def test_code_unified_diff_apply(tmp_path) -> None:
    from plugins.baselithbot.code_edit import apply_unified_diff
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "demo.txt").write_text("alpha\nbeta\ngamma\n")
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    diff = (
        "--- a/demo.txt\n+++ b/demo.txt\n"
        "@@ -1,3 +1,3 @@\n alpha\n-beta\n+BETA\n gamma\n"
    )
    out = await apply_unified_diff(diff, fs)
    assert out["status"] == "success"
    assert (root / "demo.txt").read_text() == "alpha\nBETA\ngamma\n"


# ---------------------------------------------------------------------------
# Usage ledger
# ---------------------------------------------------------------------------


def test_usage_ledger_summary_and_breakdown(tmp_path) -> None:
    from plugins.baselithbot.usage import UsageEvent, UsageLedger

    ledger = UsageLedger(ledger_path=str(tmp_path / "usage.jsonl"))
    ledger.record(
        UsageEvent(
            session_id="s1",
            agent_id="a1",
            channel="webchat",
            model="opus-4.7",
            prompt_tokens=100,
            completion_tokens=200,
            cost_usd=0.05,
            latency_ms=120,
        )
    )
    ledger.record(
        UsageEvent(
            session_id="s1",
            agent_id="a1",
            channel="webchat",
            model="opus-4.7",
            prompt_tokens=50,
            completion_tokens=80,
            cost_usd=0.02,
            latency_ms=80,
        )
    )
    summary = ledger.summary()
    assert summary["total_tokens"] == 430
    assert summary["total_cost_usd"] == 0.07
    by_session = ledger.by_session("s1")
    assert by_session["events"] == 2
    breakdown = ledger.by_model_breakdown()
    assert breakdown["opus-4.7"]["events"] == 2


# ---------------------------------------------------------------------------
# Model failover + auth rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failover_policy_skips_failed_provider() -> None:
    from plugins.baselithbot.model_routing import (
        FailoverPolicy,
        ProviderConfig,
        ProviderError,
    )

    p = FailoverPolicy(
        [
            ProviderConfig(name="primary", model="x", cooldown_seconds=0.1),
            ProviderConfig(name="secondary", model="y"),
        ]
    )

    calls: list[str] = []

    async def action(provider):
        calls.append(provider.name)
        if provider.name == "primary":
            raise ProviderError("boom")
        return {"ok": provider.name}

    out = await p.call(action)
    assert out["provider"] == "secondary"
    assert calls == ["primary", "secondary"]


def test_auth_profile_pool_round_robin() -> None:
    from plugins.baselithbot.model_routing import AuthProfile, AuthProfilePool

    pool = AuthProfilePool(
        [
            AuthProfile(name="p1", api_key="k1"),
            AuthProfile(name="p2", api_key="k2"),
        ]
    )
    picks = [pool.acquire().name for _ in range(4)]
    assert picks == ["p1", "p2", "p1", "p2"]


# ---------------------------------------------------------------------------
# Real channel adapters
# ---------------------------------------------------------------------------


def test_extra_channel_adapters_registered() -> None:
    from plugins.baselithbot.channels import build_default_registry

    known = set(build_default_registry().known())
    assert {"matrix", "signal", "irc", "twitch", "microsoft_teams"} <= known


@pytest.mark.asyncio
async def test_matrix_adapter_unconfigured() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.matrix import MatrixAdapter

    adapter = MatrixAdapter()
    out = await adapter.send(
        ChannelMessage(channel="matrix", target="!room:example.org", text="hi")
    )
    assert out["status"] == "unconfigured"


# ---------------------------------------------------------------------------
# Workspaces + agent routing
# ---------------------------------------------------------------------------


def test_workspace_manager_isolates_state() -> None:
    from plugins.baselithbot.workspace import WorkspaceConfig, WorkspaceManager

    mgr = WorkspaceManager()
    mgr.create(WorkspaceConfig(name="alpha"))
    mgr.create(WorkspaceConfig(name="beta"))
    assert {w.config.name for w in mgr.list()} == {"alpha", "beta"}
    assert mgr.sessions("alpha") is not mgr.sessions("beta")


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


def test_plugin_get_mcp_tools_includes_extra_layer() -> None:
    plugin = BaselithbotPlugin()
    names = {t["name"] for t in plugin.get_mcp_tools()}
    assert {
        "baselithbot_code_diff_apply",
        "baselithbot_code_line_edit",
        "baselithbot_code_search_replace",
        "baselithbot_code_multi_file_write",
        "baselithbot_usage_record",
        "baselithbot_usage_summary",
        "baselithbot_usage_by_session",
        "baselithbot_process_list",
        "baselithbot_process_kill",
        "baselithbot_tailscale_up",
        "baselithbot_tailscale_down",
        "baselithbot_tailscale_logout",
        "baselithbot_workspace_create",
        "baselithbot_workspace_list",
        "baselithbot_workspace_remove",
        "baselithbot_agent_list",
        "baselithbot_agent_route",
    } <= names


def test_cli_register_parser_includes_onboard() -> None:
    import argparse

    from plugins.baselithbot.cli import register_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register_parser(sub, argparse.HelpFormatter)
    args = parser.parse_args(["baselithbot", "onboard"])
    assert args.baselithbot_cmd == "onboard"


# ---------------------------------------------------------------------------
# Inbound + DM policy + slash defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbound_dispatcher_runs_handler() -> None:
    from plugins.baselithbot.inbound import InboundDispatcher, InboundEvent

    disp = InboundDispatcher()
    received: list[InboundEvent] = []

    async def handler(event: InboundEvent) -> dict[str, Any]:
        received.append(event)
        return {"status": "ok", "echo": event.text}

    disp.register("slack", handler)
    out = await disp.dispatch(InboundEvent(channel="slack", sender="alice", text="hi"))
    assert out and out[0]["status"] == "ok"
    assert received[0].text == "hi"
    assert disp.stats() == {"slack": 1}


def test_inbound_parsers_normalize_payloads() -> None:
    from plugins.baselithbot.inbound.parsers import (
        parse_discord_interaction,
        parse_slack_event,
        parse_telegram_update,
    )

    s = parse_slack_event({"event": {"user": "U1", "text": "hi"}})
    assert s.channel == "slack" and s.sender == "U1"

    t = parse_telegram_update(
        {"message": {"from": {"username": "alice"}, "text": "hello"}}
    )
    assert t.channel == "telegram" and t.sender == "alice" and t.text == "hello"

    d = parse_discord_interaction(
        {"member": {"user": {"username": "bob"}}, "data": {"name": "ping"}}
    )
    assert d.channel == "discord" and d.sender == "bob" and d.text == "ping"


def test_dm_policy_blocks_unallowlisted_sender_and_rate_limits() -> None:
    from plugins.baselithbot.policies import DMPairingPolicy

    policy = DMPairingPolicy()
    policy.configure(
        "telegram",
        allowed_senders=["alice"],
        rate_limit_window_s=60.0,
        rate_limit_max_events=2,
    )
    assert policy.evaluate("telegram", "alice").allowed is True
    assert policy.evaluate("telegram", "alice").allowed is True
    assert policy.evaluate("telegram", "alice").allowed is False
    blocked = policy.evaluate("telegram", "mallory")
    assert blocked.allowed is False
    assert "allowlist" in blocked.reason


def test_host_acl_default_and_rules() -> None:
    from plugins.baselithbot.policies import HostACL, HostACLRule

    acl = HostACL(default="deny")
    assert acl.decide("mouse_click") is False
    acl.add(HostACLRule(name="allow-clicks", action="mouse_click", decision="allow"))
    assert acl.decide("mouse_click", {"x": 10}) is True
    acl.add(
        HostACLRule(
            name="deny-fs",
            action="fs_write",
            pattern=r"/etc/",
            decision="deny",
        )
    )
    assert acl.decide("fs_write", {"path": "/etc/passwd"}) is False


@pytest.mark.asyncio
async def test_slash_default_handlers_wired() -> None:
    from plugins.baselithbot.chat_commands import ChatCommandRouter
    from plugins.baselithbot.sessions import SessionManager
    from plugins.baselithbot.slash_defaults import install_default_handlers
    from plugins.baselithbot.usage import UsageEvent, UsageLedger

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


@pytest.mark.asyncio
async def test_measure_usage_records_event() -> None:
    from plugins.baselithbot.usage import UsageLedger
    from plugins.baselithbot.usage_hooks import measure_usage

    ledger = UsageLedger()
    async with measure_usage(ledger, agent_id="x", model="opus") as info:
        info["prompt_tokens"] = 7
        info["completion_tokens"] = 11
        info["cost_usd"] = 0.001
    summary = ledger.summary()
    assert summary["total_tokens"] == 18
    assert summary["events_in_buffer"] == 1


def test_cron_scheduler_backend_label() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()
    assert sched.backend == "interval"


@pytest.mark.asyncio
async def test_rename_symbol_or_skip_if_libcst_missing(tmp_path) -> None:
    from plugins.baselithbot.code_edit import ASTRefactorError, rename_symbol
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "ws"
    root.mkdir()
    (root / "f.py").write_text("def foo():\n    foo_value = 1\n    return foo_value\n")
    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))

    try:
        out = await rename_symbol("f.py", "foo", "bar", fs)
    except ASTRefactorError as exc:
        pytest.skip(f"libcst unavailable: {exc}")

    assert out["status"] == "success"
    text = (root / "f.py").read_text()
    assert "def bar()" in text
    assert "foo_value" in text


def test_trace_span_noop_or_real() -> None:
    from plugins.baselithbot.tracing import is_tracing_enabled, trace_span

    with trace_span("baselithbot.test", foo="bar"):
        pass
    assert isinstance(is_tracing_enabled(), bool)


def test_metrics_render_returns_payload() -> None:
    from plugins.baselithbot.metrics import is_prometheus_available, render_metrics

    payload, content_type = render_metrics()
    assert isinstance(payload, bytes)
    assert content_type
    assert isinstance(is_prometheus_available(), bool)


def test_onboarding_write_block_merges_yaml(tmp_path) -> None:
    import yaml

    from plugins.baselithbot.cli import _write_onboarding_block

    target = tmp_path / "plugins.yaml"
    target.write_text(yaml.safe_dump({"baselithbot": {"enabled": False}}))

    rc = _write_onboarding_block({"enabled": True, "headless": True}, str(target))
    assert rc == 0
    data = yaml.safe_load(target.read_text())
    assert data["baselithbot"]["enabled"] is True
    assert data["baselithbot"]["headless"] is True


def test_plugin_exposes_inbound_and_dm_policy_properties() -> None:
    plugin = BaselithbotPlugin()
    assert plugin.inbound_dispatcher is not None
    assert plugin.dm_policy is not None
    assert plugin.slash_state is not None


def test_router_exposes_inbound_metrics_ws_endpoints() -> None:
    plugin = BaselithbotPlugin()
    router = plugin.create_router()
    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/inbound/{channel}" in paths
    assert "/metrics" in paths
    assert "/ws/pair" in paths


# ---------------------------------------------------------------------------
# Phase L: 24 channel adapters fully registered
# ---------------------------------------------------------------------------


def test_all_24_channels_have_first_party_adapter() -> None:
    from plugins.baselithbot.channels import (
        SUPPORTED_CHANNELS,
        build_default_registry,
    )
    from plugins.baselithbot.channels.generic import GenericWebhookAdapter

    registry = build_default_registry()
    for channel in SUPPORTED_CHANNELS:
        adapter = registry._factories[channel]({})  # type: ignore[attr-defined]
        assert not isinstance(adapter, GenericWebhookAdapter), (
            f"channel '{channel}' still using generic webhook fallback"
        )


@pytest.mark.asyncio
async def test_mattermost_adapter_unconfigured_returns_marker() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.mattermost import MattermostAdapter

    adapter = MattermostAdapter()
    out = await adapter.send(
        ChannelMessage(channel="mattermost", target="general", text="hi")
    )
    assert out["status"] == "unconfigured"


@pytest.mark.asyncio
async def test_whatsapp_adapter_unconfigured_lists_missing_creds() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.whatsapp import WhatsAppAdapter

    out = await WhatsAppAdapter().send(
        ChannelMessage(channel="whatsapp", target="+39000", text="hi")
    )
    assert out["status"] == "unconfigured"
    assert {"access_token", "phone_number_id"} <= set(out["missing"])


# ---------------------------------------------------------------------------
# Phase M: wake-word audio backend
# ---------------------------------------------------------------------------


def test_energy_threshold_wake_creates_callable() -> None:
    from plugins.baselithbot.voice import (
        EnergyThresholdWake,
        SoundDeviceAudioBackend,
    )

    backend = SoundDeviceAudioBackend()
    wake = EnergyThresholdWake(backend, threshold_rms=1500.0)
    fn = wake.make_async_callable()
    assert callable(fn)


# ---------------------------------------------------------------------------
# Phase N: ClawHub HTTP client
# ---------------------------------------------------------------------------


def test_clawhub_client_default_config() -> None:
    from plugins.baselithbot.skills import ClawHubClient, ClawHubConfig, DEFAULT_HUB_URL

    client = ClawHubClient()
    assert client.config.base_url == DEFAULT_HUB_URL

    custom = ClawHubClient(ClawHubConfig(base_url="https://example.org/hub"))
    assert custom.config.base_url == "https://example.org/hub"


class _FakeClawHubResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_data: Any = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class _FakeClawHubAsyncClient:
    def __init__(
        self,
        responses: dict[tuple[str, tuple[tuple[str, str], ...]], _FakeClawHubResponse],
        **_: Any,
    ) -> None:
        self._responses = responses

    async def __aenter__(self) -> "_FakeClawHubAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ):
        key = (
            url,
            tuple(sorted((str(k), str(v)) for k, v in (params or {}).items())),
        )
        return self._responses.get(
            key, _FakeClawHubResponse(status_code=404, json_data={"missing": url})
        )


@pytest.mark.asyncio
async def test_clawhub_install_rejects_missing_compatibility_manifest(tmp_path) -> None:
    import httpx

    from plugins.baselithbot.skills import ClawHubClient, ClawHubConfig

    responses = {
        (
            "https://clawhub.ai/api/v1/skills/acme/demo",
            (),
        ): _FakeClawHubResponse(
            json_data={"displayName": "Demo Skill", "summary": "demo"}
        ),
        (
            "https://clawhub.ai/api/v1/skills/acme/demo/file",
            (("path", "SKILL.md"),),
        ): _FakeClawHubResponse(
            text="---\nname: Demo Skill\ndescription: demo\n---\n\n# Demo\n"
        ),
        (
            "https://clawhub.ai/api/v1/skills/acme/demo/file",
            (("path", "MANIFEST.yaml"),),
        ): _FakeClawHubResponse(status_code=404, text=""),
    }

    with patch.object(
        httpx,
        "AsyncClient",
        side_effect=lambda **kwargs: _FakeClawHubAsyncClient(responses, **kwargs),
    ):
        client = ClawHubClient(ClawHubConfig(install_dir=str(tmp_path)))
        result = await client.install("acme/demo")

    assert result["status"] == "error"
    assert result["error"] == "compatibility validation failed"
    assert "compatibility section" in " ".join(result["compatibility"]["errors"])


@pytest.mark.asyncio
async def test_clawhub_install_materializes_verified_bundle(tmp_path) -> None:
    import httpx

    from plugins.baselithbot.skills import ClawHubClient, ClawHubConfig, SkillRegistry

    manifest_text = """
bundle: demo-skill
bundle_version: 1.2.3
description: Verified demo skill
compatibility:
  designed_for:
    surfaces:
      - cli
      - chat
  tested_on:
    - platform: OpenClaw
      model: GPT-5
      surface: cli
      status: pass
      date: 2026-04-17
""".strip()
    skill_text = "---\nname: Demo Skill\ndescription: verified demo\n---\n\n# Demo\n"

    responses = {
        (
            "https://clawhub.ai/api/v1/skills/acme/demo",
            (),
        ): _FakeClawHubResponse(
            json_data={
                "displayName": "Demo Skill",
                "summary": "verified demo",
                "version": "1.2.3",
            }
        ),
        (
            "https://clawhub.ai/api/v1/skills/acme/demo/file",
            (("path", "SKILL.md"),),
        ): _FakeClawHubResponse(text=skill_text),
        (
            "https://clawhub.ai/api/v1/skills/acme/demo/file",
            (("path", "MANIFEST.yaml"),),
        ): _FakeClawHubResponse(text=manifest_text),
    }

    with patch.object(
        httpx,
        "AsyncClient",
        side_effect=lambda **kwargs: _FakeClawHubAsyncClient(responses, **kwargs),
    ):
        client = ClawHubClient(ClawHubConfig(install_dir=str(tmp_path)))
        registry = SkillRegistry()
        result = await client.install("acme/demo", registry=registry)

    assert result["status"] == "success"
    installed = registry.get("acme/demo")
    assert installed is not None
    assert installed.entrypoint is not None
    installed_dir = tmp_path / "acme__demo"
    assert installed_dir.is_dir()
    assert (installed_dir / "SKILL.md").read_text() == skill_text
    assert (installed_dir / "MANIFEST.yaml").read_text() == manifest_text
    assert installed.metadata["compatibility"]["compatible"] is True


# ---------------------------------------------------------------------------
# Phase O: A2UI extra widgets
# ---------------------------------------------------------------------------


def test_canvas_extra_widgets_serialize() -> None:
    from plugins.baselithbot.canvas import (
        CanvasChart,
        CanvasForm,
        CanvasProgress,
        CanvasTable,
        FormField,
    )

    form = CanvasForm(
        submit_action="submit",
        fields=[FormField(name="email", type="email", required=True)],
    )
    table = CanvasTable(columns=["a", "b"], rows=[[1, 2], [3, 4]])
    chart = CanvasChart(chart_type="bar", series=[{"label": "s", "data": [1]}])
    progress = CanvasProgress(value=0.42, label="loading")

    for widget in (form, table, chart, progress):
        dumped = widget.model_dump()
        assert dumped["id"]
        assert dumped["type"]


# ---------------------------------------------------------------------------
# Phase P: signature verifiers
# ---------------------------------------------------------------------------


def test_slack_signature_verifier_round_trip() -> None:
    import hashlib
    import hmac

    from plugins.baselithbot.inbound import verify_slack_signature

    secret = "shh"
    body = b'{"event": "x"}'
    timestamp = "1700000000"
    base = f"v0:{timestamp}:".encode("utf-8") + body
    digest = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    sig = f"v0={digest}"

    assert verify_slack_signature(secret, timestamp, body, sig) is True
    assert verify_slack_signature(secret, timestamp, body, "v0=bad") is False
    assert verify_slack_signature(secret, timestamp, body, "wrong-prefix") is False


def test_github_signature_verifier_rejects_mismatch() -> None:
    import hashlib
    import hmac

    from plugins.baselithbot.inbound import verify_github_signature

    secret = "topsecret"
    body = b"payload"
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_github_signature(secret, body, f"sha256={digest}") is True
    assert verify_github_signature(secret, body, "sha256=deadbeef") is False
    assert verify_github_signature(secret, body, "no-prefix") is False


def test_telegram_secret_token_verifier() -> None:
    from plugins.baselithbot.inbound import verify_telegram_secret_token

    assert verify_telegram_secret_token("abc", "abc") is True
    assert verify_telegram_secret_token("abc", "xyz") is False
    assert verify_telegram_secret_token("abc", None) is False


# ---------------------------------------------------------------------------
# Performance + security hardening
# ---------------------------------------------------------------------------


def test_secret_redaction_masks_known_keys() -> None:
    from plugins.baselithbot.secret_redaction import redact_payload

    out = redact_payload(
        {
            "bot_token": "1234567890abcdef",
            "webhook_url": "https://hooks.example/xyz/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            "text": "hello",
            "nested": {"api_key": "k", "ok": "fine"},
        }
    )
    assert out["bot_token"] == "<redacted>"
    assert out["webhook_url"] == "<redacted>"
    assert out["text"] == "hello"
    assert out["nested"]["api_key"] == "<redacted>"
    assert out["nested"]["ok"] == "fine"


def test_secret_redaction_masks_long_tokens_in_strings() -> None:
    from plugins.baselithbot.secret_redaction import redact_payload

    raw = "Authorization: Bearer abcdef1234567890abcdef1234567890abcdef"
    out = redact_payload(raw)
    assert "Bearer <redacted>" in out


@pytest.mark.asyncio
async def test_audit_logger_batch_flush(tmp_path) -> None:
    from plugins.baselithbot.computer_use import AuditLogger

    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path), batch_size=4, flush_interval_seconds=60.0)
    for i in range(3):
        audit.record("ping", n=i)
    assert not audit_path.exists() or audit_path.read_text() == ""
    audit.record("ping", n=3)
    # batch threshold reached -> flushed
    assert audit_path.is_file()
    assert audit_path.read_text().count("\n") == 4


def test_audit_logger_redacts_sensitive_keys(tmp_path) -> None:
    from plugins.baselithbot.computer_use import AuditLogger

    audit_path = tmp_path / "audit.log"
    audit = AuditLogger(str(audit_path), batch_size=1)
    audit.record("send", bot_token="should-be-hidden", target="user-1")
    contents = audit_path.read_text()
    assert "should-be-hidden" not in contents
    assert "<redacted>" in contents
    assert "user-1" in contents


@pytest.mark.asyncio
async def test_filesystem_rejects_symlink_escape(tmp_path) -> None:
    import os

    from plugins.baselithbot.computer_use import (
        AuditLogger,
        ComputerUseConfig,
        ComputerUseError,
    )
    from plugins.baselithbot.filesystem import ScopedFileSystem

    root = tmp_path / "scope"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("oops")
    link = root / "leak"
    os.symlink(outside, link)

    cfg = ComputerUseConfig(
        enabled=True, allow_filesystem=True, filesystem_root=str(root)
    )
    fs = ScopedFileSystem(cfg, AuditLogger(None))
    with pytest.raises(ComputerUseError, match="escapes filesystem_root|symlink"):
        await fs.read("leak")


def test_filesystem_rejects_null_byte_in_path(tmp_path) -> None:
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
    with pytest.raises(ComputerUseError, match="null byte"):
        fs._resolve("nope\x00")


def test_channel_https_url_validation_rejects_http() -> None:
    from plugins.baselithbot.channels import ChannelMessage
    from plugins.baselithbot.channels.slack import SlackAdapter

    adapter = SlackAdapter({"webhook_url": "http://hooks.slack.com/xyz"})
    assert adapter.is_configured() is False

    adapter_https = SlackAdapter({"webhook_url": "https://hooks.slack.com/xyz"})
    assert adapter_https.is_configured() is True

    adapter_local = SlackAdapter({"webhook_url": "http://localhost:8000/hook"})
    assert adapter_local.is_configured() is True
    del ChannelMessage  # silence unused import


@pytest.mark.asyncio
async def test_inbound_dispatcher_runs_handlers_in_parallel() -> None:
    import asyncio as _asyncio
    import time as _time

    from plugins.baselithbot.inbound import InboundDispatcher, InboundEvent

    disp = InboundDispatcher()

    async def slow_a(event):
        del event
        await _asyncio.sleep(0.1)
        return {"who": "a"}

    async def slow_b(event):
        del event
        await _asyncio.sleep(0.1)
        return {"who": "b"}

    disp.register("multi", slow_a)
    disp.register("multi", slow_b)
    started = _time.time()
    out = await disp.dispatch(InboundEvent(channel="multi", text=""))
    elapsed = _time.time() - started
    assert {r["who"] for r in out} == {"a", "b"}
    assert elapsed < 0.18, f"handlers ran sequentially: {elapsed:.3f}s"


def test_cron_sleep_until_next_returns_min_interval() -> None:
    from plugins.baselithbot.cron import CronScheduler

    sched = CronScheduler()

    async def noop():
        return None

    sched.add_interval("a", noop, seconds=2)
    sched.add_interval("b", noop, seconds=10)
    sleep_for = sched._sleep_until_next(now=__import__("time").time())
    assert 0 < sleep_for <= 2.0


@pytest.mark.asyncio
async def test_http_pool_reuses_client() -> None:
    from plugins.baselithbot.http_pool import HTTPClientPool

    pool = HTTPClientPool()
    try:
        c1 = await pool.acquire(timeout=5.0)
        c2 = await pool.acquire(timeout=5.0)
        assert c1 is c2
        c3 = await pool.acquire(timeout=10.0)
        assert c3 is not c1
    finally:
        await pool.close_all()


def test_stealth_pick_user_agent_uses_secrets() -> None:
    import inspect

    from plugins.baselithbot.stealth import pick_user_agent
    from plugins.baselithbot.types import StealthConfig

    src = inspect.getsource(pick_user_agent)
    assert "secrets.choice" in src
    cfg = StealthConfig()
    assert pick_user_agent(cfg) in cfg.user_agents


@pytest.mark.asyncio
async def test_desktop_vision_jpeg_format_validated() -> None:
    from plugins.baselithbot.computer_use import AuditLogger, ComputerUseConfig
    from plugins.baselithbot.desktop_vision import DesktopVision

    cfg = ComputerUseConfig(enabled=True, allow_screenshot=True)
    vision = DesktopVision(cfg, AuditLogger(None))
    with pytest.raises(ValueError, match="unsupported image_format"):
        await vision.screenshot(image_format="GIF")


# ---------------------------------------------------------------------------
# Agents tab — default seeding, custom CRUD, dispatch, persistence
# ---------------------------------------------------------------------------


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
