"""Fail-closed defaults for the MCP autonomy gate."""

import pytest

import core.config.mcp as mcp_config_module
from core.mcp.server import MCPServer
from core.mcp.types import MCPTool
from core.orchestration.autonomy import DESTRUCTIVE, AutonomyLevel, AutonomyPolicy


async def _noop() -> str:
    return "ok"


@pytest.fixture
def fresh_mcp_config(monkeypatch):
    """Force MCPConfig re-read from env for each test."""
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)
    yield
    monkeypatch.setattr(mcp_config_module, "_mcp_config", None)


def test_mcp_tool_category_defaults_to_destructive():
    tool = MCPTool(name="t", description="d", input_schema={})
    assert tool.category == DESTRUCTIVE


def test_register_tool_category_defaults_to_destructive(fresh_mcp_config):
    server = MCPServer(name="s", version="1")
    server.register_tool(
        name="undeclared", description="d", input_schema={}, handler=_noop
    )
    assert server._tools["undeclared"].category == DESTRUCTIVE


def test_server_default_policy_is_supervised(monkeypatch, fresh_mcp_config):
    monkeypatch.delenv("MCP_AUTONOMY_LEVEL", raising=False)
    server = MCPServer(name="s", version="1")
    assert server._autonomy_policy is not None
    assert server._autonomy_policy.level == AutonomyLevel.SUPERVISED


def test_server_policy_level_from_env(monkeypatch, fresh_mcp_config):
    monkeypatch.setenv("MCP_AUTONOMY_LEVEL", "fully_autonomous")
    server = MCPServer(name="s", version="1")
    assert server._autonomy_policy.level == AutonomyLevel.FULLY_AUTONOMOUS


def test_server_unknown_level_falls_back_to_supervised(monkeypatch, fresh_mcp_config):
    monkeypatch.setenv("MCP_AUTONOMY_LEVEL", "yolo")
    server = MCPServer(name="s", version="1")
    assert server._autonomy_policy.level == AutonomyLevel.SUPERVISED


def test_explicit_policy_still_wins(fresh_mcp_config):
    policy = AutonomyPolicy(level=AutonomyLevel.SEMI_AUTONOMOUS)
    server = MCPServer(name="s", version="1", autonomy_policy=policy)
    assert server._autonomy_policy is policy
