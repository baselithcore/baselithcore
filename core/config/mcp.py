"""
MCP Configuration.

Settings for the Model Context Protocol (MCP) server and client.
"""

import logging
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class MCPConfig(BaseSettings):
    """
    Configuration for MCP Server and Client.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === Server Settings ===
    mcp_server_name: str = Field(default="baselith-core", alias="MCP_SERVER_NAME")
    mcp_server_version: str = Field(default="2.0.0", alias="MCP_SERVER_VERSION")

    # === Transport Settings ===
    mcp_stdio_transport_enabled: bool = Field(
        default=True, alias="MCP_STDIO_TRANSPORT_ENABLED"
    )
    mcp_sse_transport_enabled: bool = Field(
        default=False, alias="MCP_SSE_TRANSPORT_ENABLED"
    )

    # === Tool Settings ===
    mcp_execute_code_timeout: int = Field(
        default=30, alias="MCP_EXECUTE_CODE_TIMEOUT", ge=1
    )
    mcp_rag_default_top_k: int = Field(default=5, alias="MCP_RAG_DEFAULT_TOP_K", ge=1)


# Global instance
_mcp_config: Optional[MCPConfig] = None


def get_mcp_config() -> MCPConfig:
    """Get or create the global MCP configuration instance."""
    global _mcp_config
    if _mcp_config is None:
        _mcp_config = MCPConfig()
        logger.info(
            f"Initialized MCPConfig (server_name={_mcp_config.mcp_server_name})"
        )
    return _mcp_config
