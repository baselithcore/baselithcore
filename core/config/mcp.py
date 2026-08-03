"""
MCP Configuration.

Settings for the Model Context Protocol (MCP) server and client.
"""

import logging
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class MCPConfig(BaseSettings):
    """
    Configuration for MCP Server and Client.
    """

    model_config = SettingsConfigDict(
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

    # === Streamable HTTP transport (MCP spec 2025-06-18) ===
    # Off by default: enabling exposes the MCP server on the API surface.
    mcp_http_transport_enabled: bool = Field(
        default=False, alias="MCP_HTTP_TRANSPORT_ENABLED"
    )
    mcp_http_path: str = Field(default="/mcp", alias="MCP_HTTP_PATH")
    # Fail-closed: HTTP callers must present credentials accepted by the
    # central AuthManager (Bearer JWT / OIDC / API key). Only disable for
    # trusted localhost development.
    mcp_http_require_auth: bool = Field(default=True, alias="MCP_HTTP_REQUIRE_AUTH")
    # Comma-separated allowlist of browser Origins (DNS-rebinding defense).
    # Requests without an Origin header (non-browser clients) always pass.
    mcp_http_allowed_origins: str = Field(default="", alias="MCP_HTTP_ALLOWED_ORIGINS")
    mcp_http_session_ttl_seconds: int = Field(
        default=3600, alias="MCP_HTTP_SESSION_TTL_SECONDS", ge=1
    )
    # Cap on live sessions a single identity may hold at once, so a client
    # cannot mint sessions unbounded and pin memory for the whole TTL. 0
    # disables the cap.
    mcp_http_max_sessions_per_client: int = Field(
        default=64, alias="MCP_HTTP_MAX_SESSIONS_PER_CLIENT", ge=0
    )

    # Maximum entries returned by one tools/list, resources/list or
    # resources/templates/list page; the client pages on with `nextCursor`.
    mcp_list_page_size: int = Field(default=100, alias="MCP_LIST_PAGE_SIZE", ge=1)

    # === Caching hints (spec 2026-07-28 CacheableResult) ===
    # Freshness hint on list/read results, in milliseconds. 0 means "always
    # stale": correct for a server whose tools or resources change per request.
    mcp_cache_ttl_ms: int = Field(default=60000, alias="MCP_CACHE_TTL_MS", ge=0)
    # "private" is the safe default: listings and reads may be filtered by the
    # authenticated identity, and a shared cache would leak them across
    # authorization contexts. Set "public" only when every caller sees the
    # same primitives.
    mcp_cache_scope: Literal["public", "private"] = Field(
        default="private", alias="MCP_CACHE_SCOPE"
    )
    # === Multi round-trip requests (MRTR) ===
    # HMAC key sealing `requestState`, which travels through the client and is
    # therefore attacker-controlled. Unset means a random per-process key: fine
    # for one instance, but a multi-replica deployment MUST set a shared secret
    # or a retry landing on another replica will be rejected.
    mcp_request_state_secret: SecretStr | None = Field(
        default=None, alias="MCP_REQUEST_STATE_SECRET"
    )
    mcp_request_state_ttl_seconds: int = Field(
        default=300, alias="MCP_REQUEST_STATE_TTL_SECONDS", ge=1
    )

    # === Tasks extension (io.modelcontextprotocol/tasks) ===
    # How long a task handle stays resolvable, and how often the client should
    # poll it. Both are hints carried in the CreateTaskResult.
    mcp_task_ttl_ms: int = Field(default=3_600_000, alias="MCP_TASK_TTL_MS", ge=1)
    mcp_task_poll_interval_ms: int = Field(
        default=1000, alias="MCP_TASK_POLL_INTERVAL_MS", ge=1
    )

    # Optional natural-language guidance returned by `server/discover`.
    mcp_server_instructions: str = Field(default="", alias="MCP_SERVER_INSTRUCTIONS")

    # Comma-separated issuer URLs advertised as `authorization_servers` in the
    # RFC 9728 protected-resource metadata. Defaults to the configured OIDC
    # issuer when empty.
    mcp_http_authorization_servers: str = Field(
        default="", alias="MCP_HTTP_AUTHORIZATION_SERVERS"
    )

    # === Tool Settings ===
    mcp_execute_code_timeout: int = Field(
        default=30, alias="MCP_EXECUTE_CODE_TIMEOUT", ge=1
    )
    mcp_rag_default_top_k: int = Field(default=5, alias="MCP_RAG_DEFAULT_TOP_K", ge=1)

    # === Client Settings ===
    # Upper bound (seconds) on waiting for a response from an external MCP
    # server. Guards against a hung or unresponsive server blocking the agent
    # loop indefinitely.
    mcp_client_request_timeout: float = Field(
        default=30.0, alias="MCP_CLIENT_REQUEST_TIMEOUT", gt=0
    )

    # Fail-closed by default: the SSRF guard rejects private/loopback/
    # link-local MCP server hosts. Enable only for trusted local development
    # against an MCP server on localhost/the internal network.
    mcp_allow_internal_endpoints: bool = Field(
        default=False, alias="MCP_ALLOW_INTERNAL_ENDPOINTS"
    )

    # Comma-separated allowlist of executable basenames that MCPClient may
    # spawn for stdio servers. A custom `command` whose argv[0] basename is
    # not in this list is rejected — manifests/config cannot make the client
    # exec arbitrary binaries.
    mcp_allowed_commands: str = Field(
        default="python,python3,node,npx,uvx,uv,deno,bun,bunx",
        alias="MCP_ALLOWED_COMMANDS",
    )

    @property
    def allowed_command_basenames(self) -> frozenset[str]:
        """Parsed, normalized view of ``mcp_allowed_commands``."""
        return frozenset(
            item.strip().lower()
            for item in self.mcp_allowed_commands.split(",")
            if item.strip()
        )

    @property
    def authorization_server_list(self) -> tuple[str, ...]:
        """Issuers advertised in the protected-resource metadata.

        Falls back to the configured OIDC issuer so a deployment that already
        federates to an IdP gets correct discovery without extra settings.
        """
        explicit = tuple(
            item.strip()
            for item in self.mcp_http_authorization_servers.split(",")
            if item.strip()
        )
        if explicit:
            return explicit
        from core.config import get_security_config

        issuer = getattr(get_security_config(), "oidc_issuer", None)
        return (issuer,) if issuer else ()

    @property
    def http_allowed_origin_set(self) -> frozenset[str]:
        """Parsed view of ``mcp_http_allowed_origins``."""
        return frozenset(
            item.strip()
            for item in self.mcp_http_allowed_origins.split(",")
            if item.strip()
        )


# Global instance
_mcp_config: MCPConfig | None = None


def get_mcp_config() -> MCPConfig:
    """Get or create the global MCP configuration instance."""
    global _mcp_config
    if _mcp_config is None:
        _mcp_config = MCPConfig()
        logger.info(
            f"Initialized MCPConfig (server_name={_mcp_config.mcp_server_name})"
        )
    return _mcp_config
