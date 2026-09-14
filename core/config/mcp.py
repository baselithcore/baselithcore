"""
MCP Configuration.

Settings for the Model Context Protocol (MCP) server and client.
"""

import logging
from typing import Literal, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: Smallest honourable value for ``MCP_MAX_TOOL_RESULT_BYTES``.
#:
#: When a ``tools/call`` result is over the cap, the truncation path in
#: :meth:`core.mcp.tool_handlers.ToolHandlerMixin._cap_result` does not just
#: cut the payload: it emits a replacement envelope -- a human-readable
#: ``[truncated: ...]`` notice plus a ``_meta`` record carrying
#: ``originalBytes``/``maxBytes``. That envelope alone serializes to roughly
#: 220-240 bytes. A cap below that cannot be met at all: the bisection keeps
#: zero characters of payload and the result still exceeds the limit, with
#: nothing telling the operator the cap is not holding. 512 leaves the
#: envelope room plus a usable slice of the tool's own output.
MIN_TOOL_RESULT_BYTES = 512


class MCPServerSpec(BaseModel):
    """Declarative description of one external MCP server to mount.

    Exactly one of ``command`` (stdio transport) or ``url`` (Streamable
    HTTP transport) must be set. ``command`` is subject to the
    ``mcp_allowed_commands`` executable allowlist — a non-allowlisted
    command is refused at mount time (fail-closed). ``env`` entries are
    plain strings passed to the spawned process; do not put credentials
    here — prefer the server reading its own secret store.

    Attributes:
        command: Executable for a stdio server (e.g. ``"python"``).
        args: Arguments appended after ``command``.
        env: Extra environment variables for the stdio server process.
        url: Streamable HTTP endpoint of a remote server.
        autonomy_category: Category stamped on every tool the server
            exposes (``read_only`` | ``mutating`` | ``destructive`` |
            ``external_side_effect`` | ``self_modify``), consulted by the
            approval gate.
    """

    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    autonomy_category: str = "read_only"

    @model_validator(mode="after")
    def _exactly_one_transport(self) -> Self:
        if bool(self.command) == bool(self.url):
            raise ValueError(
                "exactly one of 'command' (stdio) or 'url' (HTTP) must be set"
            )
        return self


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

    mcp_autonomy_level: str = Field(
        default="supervised",
        alias="MCP_AUTONOMY_LEVEL",
        description=(
            "Autonomy level applied when no explicit policy is passed to "
            "MCPServer: supervised | semi_autonomous | fully_autonomous. "
            "MCP transports have no human-approval channel, so gated "
            "categories are rejected outright (fail-closed). Set "
            "fully_autonomous only when the MCP client itself enforces "
            "human approval (e.g. Claude Desktop tool prompts)."
        ),
    )

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
    # Capability an authenticated caller must hold to reach the MCP surface.
    # Authenticating is not authorizing: without this, a least-privilege scoped
    # API key minted for an unrelated resource (say webhooks:write) reached the
    # whole tool catalog and tools/call. Granted by default to the admin,
    # service, user and job roles, so role-based identities are unaffected.
    # Empty disables the capability check.
    mcp_http_required_scope: str = Field(
        default="mcp:invoke", alias="MCP_HTTP_REQUIRED_SCOPE"
    )
    # Per-identity request budget for the MCP endpoint. Each request spawns
    # server-side work (and a streaming task), so an authenticated caller must
    # not be able to flood it unmetered. 0 disables the limit.
    mcp_http_rate_limit_per_minute: int = Field(
        default=120, alias="MCP_HTTP_RATE_LIMIT_PER_MINUTE", ge=0
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

    # Canonical resource identifier for this MCP endpoint (RFC 8707 / RFC 9728):
    # the value published as `resource` in the protected-resource metadata AND
    # the value a token's `aud` is checked against — one setting, so the two
    # can never disagree. Unset, it is derived from the request's base URL,
    # which comes from the Host header: behind a proxy that does not pin the
    # host (ALLOWED_HOSTS unset), a caller controls what the endpoint claims to
    # be. Set it to the public URL, e.g. https://api.example.com/mcp.
    mcp_resource_url: str = Field(default="", alias="MCP_RESOURCE_URL")

    # Whether an OAuth access token must name this MCP endpoint in its ``aud``
    # claim (RFC 8707 resource indicators / RFC 9728). When on, a token whose
    # ``aud`` names a *different* resource is refused — that is a token minted
    # for somebody else, replayed here — and so is a token carrying no ``aud``
    # at all. ``None`` (the default) resolves at request time to the runtime
    # posture: enforced in production, off elsewhere. Setting it to ``false``
    # is the operator override that stands the whole check down, which matters
    # because JWT_AUDIENCE is pinned per deployment: if the issued audience is
    # not this endpoint's resource URL, *every* token mismatches and the
    # endpoint is unreachable until the tokens are reissued (or MCP_RESOURCE_URL
    # is set to the audience they already carry). API keys are never subject to
    # the check: they are not OAuth tokens and carry no audience.
    mcp_require_token_audience: bool | None = Field(
        default=None, alias="MCP_REQUIRE_TOKEN_AUDIENCE"
    )

    # === Tool Settings ===
    # Server-side deadline on one ``tools/call`` handler. Without it a hung
    # tool holds the request (and, on HTTP, the connection) indefinitely. 0
    # disables the deadline.
    mcp_tool_call_timeout_seconds: float = Field(
        default=60.0, alias="MCP_TOOL_CALL_TIMEOUT_SECONDS", ge=0
    )
    # Upper bound on the serialized size of one ``tools/call`` result. A larger
    # result is truncated with an explicit notice rather than refused: the call
    # succeeded, and the model is told what it is missing. 0 disables the cap;
    # any other value must clear MIN_TOOL_RESULT_BYTES, the cost of the
    # truncation envelope itself.
    mcp_max_tool_result_bytes: int = Field(
        default=1_048_576, alias="MCP_MAX_TOOL_RESULT_BYTES", ge=0
    )
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

    # === Declarative external server registry ===
    # Named external MCP servers, mountable by calling
    # core.mcp.declarative.mount_configured_servers (a library entry point —
    # no automatic startup wiring invokes it). Env-configurable via the
    # MCP_SERVERS variable holding a JSON object, e.g.
    #   MCP_SERVERS='{"weather": {"command": "python",
    #                 "args": ["weather_server.py"],
    #                 "autonomy_category": "read_only"}}'
    # Stdio commands are still gated by the mcp_allowed_commands allowlist.
    mcp_servers: dict[str, MCPServerSpec] = Field(
        default_factory=dict,
        alias="MCP_SERVERS",
        description=(
            "JSON mapping of server name -> MCPServerSpec "
            "(command/args/env for stdio, or url for Streamable HTTP, "
            "plus an autonomy_category applied to the server's tools)."
        ),
    )

    @field_validator("mcp_max_tool_result_bytes")
    @classmethod
    def _tool_result_cap_clears_the_envelope(cls, value: int) -> int:
        """Reject a tool-result cap the truncation envelope cannot fit inside.

        Truncation does not merely shorten the payload: it replaces the result
        with a notice block plus a ``_meta`` record that together cost roughly
        220-240 serialized bytes. Below that, the cap is unenforceable -- the
        emitted "capped" result is still over the limit and nothing says so --
        which is worse than no cap at all, because the operator believes one is
        in force. 0 keeps its documented meaning: no cap.

        Args:
            value: Candidate value for ``MCP_MAX_TOOL_RESULT_BYTES``.

        Returns:
            The value unchanged when it is enforceable.

        Raises:
            ValueError: The value is positive but under
                :data:`MIN_TOOL_RESULT_BYTES`.

        Note:
            ``validate_assignment`` is deliberately **not** enabled on this
            class, so assigning a smaller value to an already-built config
            would skip this check. That is accepted: the class has exactly one
            construction site (:func:`get_mcp_config`, fully validated), no
            code assigns to the field, and nothing builds it through the
            validation-skipping idioms (``model_construct`` /
            ``model_copy(update=...)``). Enabling it here alone would also give
            false assurance about the path that actually reads the value —
            ``ToolHandlerMixin._cap_result`` resolves it with ``getattr`` on a
            duck-typed config object, which Pydantic cannot police at all.
        """
        if 0 < value < MIN_TOOL_RESULT_BYTES:
            raise ValueError(
                f"MCP_MAX_TOOL_RESULT_BYTES={value} is below the "
                f"{MIN_TOOL_RESULT_BYTES}-byte floor: the truncation notice "
                "and its _meta envelope alone cost ~220-240 bytes, so a cap "
                "this small cannot be honoured. Use 0 to disable the cap, or "
                f"a value >= {MIN_TOOL_RESULT_BYTES}."
            )
        return value

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
