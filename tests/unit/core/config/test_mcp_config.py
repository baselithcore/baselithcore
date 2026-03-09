"""
Tests for MCP Configuration.
"""

import os
from unittest.mock import patch

from core.config import get_mcp_config, MCPConfig


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

    def test_env_overrides(self) -> None:
        """Test environment variable overrides."""
        env_vars = {
            "MCP_SERVER_NAME": "custom-server",
            "MCP_SERVER_VERSION": "1.5.0",
            "MCP_EXECUTE_CODE_TIMEOUT": "60",
        }

        with patch.dict(os.environ, env_vars):
            config = MCPConfig()

            assert config.mcp_server_name == "custom-server"
            assert config.mcp_server_version == "1.5.0"
            assert config.mcp_execute_code_timeout == 60

    def test_singleton(self) -> None:
        """Test singleton accessor."""
        config1 = get_mcp_config()
        config2 = get_mcp_config()

        assert config1 is config2
