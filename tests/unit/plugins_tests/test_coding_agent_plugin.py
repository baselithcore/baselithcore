from core.agents.coding import CodingAgent as CoreCodingAgent
from core.agents.coding.agent import CodeLanguage as CoreCodeLanguage
from core.agents.coding_tools import (
    register_coding_tools as core_register_coding_tools,
)
from plugins.coding_agent import (
    CodeLanguage,
    CodingAgent,
    register_coding_tools,
)
from plugins.coding_agent.plugin import CodingAgentPlugin


def test_legacy_core_imports_resolve_to_plugin_exports() -> None:
    assert CoreCodingAgent is CodingAgent
    assert CoreCodeLanguage is CodeLanguage
    assert core_register_coding_tools is register_coding_tools


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
