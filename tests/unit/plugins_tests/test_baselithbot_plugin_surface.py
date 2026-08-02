"""Unit tests for the Baselithbot plugin — manifest, MCP tool surface, CLI."""

from __future__ import annotations

import pytest

from plugins.baselithbot import (
    BaselithbotPlugin,
    StealthConfig,
)


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


def test_plugin_get_flow_handlers_binds_intent() -> None:
    plugin = BaselithbotPlugin()
    handlers = plugin.get_flow_handlers()
    assert "baselithbot_browse" in handlers
    assert callable(handlers["baselithbot_browse"])


def test_cli_register_parser_adds_subcommand() -> None:
    import argparse

    from plugins.baselithbot.diagnostics.cli import register_parser

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="cmd")
    register_parser(subparsers, argparse.HelpFormatter)
    args = parser.parse_args(["baselithbot", "run", "open hn"])
    assert args.cmd == "baselithbot"
    assert args.baselithbot_cmd == "run"
    assert args.goal == "open hn"


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

    from plugins.baselithbot.diagnostics.cli import register_parser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register_parser(sub, argparse.HelpFormatter)
    args = parser.parse_args(["baselithbot", "onboard"])
    assert args.baselithbot_cmd == "onboard"


def test_onboarding_write_block_merges_yaml(tmp_path) -> None:
    import yaml

    from plugins.baselithbot.diagnostics.cli import _write_onboarding_block

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
    from plugins.baselithbot.diagnostics.doctor import run_doctor

    report = await run_doctor()
    assert "platform" in report
    assert "python_dependencies" in report
    assert "system_binaries" in report
