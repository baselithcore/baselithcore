"""
Tests for MCP Configuration.
"""

import json
import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from core.config import MCPConfig, get_mcp_config
from core.config.mcp import MIN_TOOL_RESULT_BYTES


class TestMCPConfig:
    """Test suite for MCP Configuration."""

    def test_defaults(self) -> None:
        """Test default configuration values."""
        config = MCPConfig()

        assert config.mcp_server_name == "baselith-core"
        assert config.mcp_server_version == "2.0.0"
        assert config.mcp_stdio_transport_enabled is True
        assert config.mcp_sse_transport_enabled is False
        assert config.mcp_execute_code_timeout == 30
        assert config.mcp_rag_default_top_k == 5
        assert config.mcp_client_request_timeout == 30.0

    def test_env_overrides(self) -> None:
        """Test environment variable overrides."""
        env_vars = {
            "MCP_SERVER_NAME": "custom-server",
            "MCP_SERVER_VERSION": "1.5.0",
            "MCP_EXECUTE_CODE_TIMEOUT": "60",
            "MCP_CLIENT_REQUEST_TIMEOUT": "5.5",
        }

        with patch.dict(os.environ, env_vars):
            config = MCPConfig()

            assert config.mcp_server_name == "custom-server"
            assert config.mcp_server_version == "1.5.0"
            assert config.mcp_execute_code_timeout == 60
            assert config.mcp_client_request_timeout == 5.5

    def test_singleton(self) -> None:
        """Test singleton accessor."""
        config1 = get_mcp_config()
        config2 = get_mcp_config()

        assert config1 is config2


class TestToolResultCapFloor:
    """The tool-result cap must not be settable below its own envelope.

    ``ToolHandlerMixin._cap_result`` replaces an over-cap result with a notice
    block plus a ``_meta`` record. That envelope alone serializes to roughly
    220-240 bytes, so a cap configured below ~250 cannot be honoured: the
    bisection keeps zero characters of payload and the "capped" result is
    *still* over the limit, silently. A floor makes that unrepresentable.
    """

    def test_below_floor_is_rejected(self) -> None:
        """A cap smaller than the envelope is a configuration error."""
        with patch.dict(os.environ, {"MCP_MAX_TOOL_RESULT_BYTES": "200"}):
            with pytest.raises(ValidationError) as excinfo:
                MCPConfig()

        message = str(excinfo.value)
        assert "MCP_MAX_TOOL_RESULT_BYTES" in message
        assert str(MIN_TOOL_RESULT_BYTES) in message

    def test_zero_still_disables_the_cap(self) -> None:
        """0 is the documented "no cap" value and stays legal."""
        with patch.dict(os.environ, {"MCP_MAX_TOOL_RESULT_BYTES": "0"}):
            assert MCPConfig().mcp_max_tool_result_bytes == 0

    def test_floor_itself_is_accepted(self) -> None:
        """The floor is inclusive."""
        with patch.dict(
            os.environ, {"MCP_MAX_TOOL_RESULT_BYTES": str(MIN_TOOL_RESULT_BYTES)}
        ):
            assert MCPConfig().mcp_max_tool_result_bytes == MIN_TOOL_RESULT_BYTES

    def test_floor_exceeds_the_real_envelope(self) -> None:
        """Pin the floor against the envelope the truncation path emits.

        Computed the way ``_cap_result`` builds it, so a future edit that
        grows the notice or ``_meta`` fails here instead of in production.
        """
        original, limit = 10**12, MIN_TOOL_RESULT_BYTES
        notice = (
            f"\n\n[truncated: the result was {original} bytes, over the "
            f"{limit}-byte cap; the rest was dropped]"
        )
        envelope = {
            "content": [{"type": "text", "text": notice}],
            "isError": False,
            "_meta": {
                "truncated": True,
                "originalBytes": original,
                "maxBytes": limit,
            },
        }
        assert len(json.dumps(envelope).encode()) < MIN_TOOL_RESULT_BYTES
