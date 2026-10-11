from baselith.plugins.coding_agent.plugin import CodingAgentPlugin


def test_coding_agent_plugin_exposes_manifest_metadata() -> None:
    plugin = CodingAgentPlugin()

    assert plugin.metadata.name == "coding-agent"
    assert "coding" in plugin.metadata.tags


def test_coding_agent_plugin_exposes_mcp_tools() -> None:
    plugin = CodingAgentPlugin()

    tools = plugin.get_mcp_tools()
    tool_names = {tool["name"] for tool in tools}

    assert "fix_code" in tool_names
    assert "generate_code" in tool_names
    assert "refactor_code" in tool_names
