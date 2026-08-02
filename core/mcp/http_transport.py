"""Streamable HTTP transport for the MCP server — dual-era.

Exposes an :class:`~core.mcp.server.MCPServer` over a single HTTP endpoint:

* ``POST {path}`` — one JSON-RPC message per request (batching was removed in
  2025-06-18; arrays are rejected). Requests are answered as
  ``application/json``; notifications get ``202 Accepted`` with no body.
* ``DELETE {path}`` — explicit session termination (legacy era only).
* ``GET {path}`` — ``405``: this server does not offer a server-initiated
  event stream (allowed by the spec; 2026-07-28 removed the GET stream
  outright).

**Modern era (2026-07-28)** — a request carrying per-request ``_meta`` is
served statelessly: no session is required or minted, a stale
``Mcp-Session-Id`` is ignored, and the standard headers
(``MCP-Protocol-Version``, ``Mcp-Method``, ``Mcp-Name``) are validated against
the body, since an intermediary routing on a header while the server executes
the body value is a confused-deputy split. Mismatch or missing header →
``400`` + ``-32020``; unsupported version → ``400`` + ``-32022``; unknown
method → ``404`` + ``-32601``.

**Legacy era** — ``initialize`` mints an ``Mcp-Session-Id`` echoed as a
response header; every subsequent request must carry it and an unknown or
expired id yields ``404`` (the client then re-initializes). Non-initialize
requests carrying an unsupported ``MCP-Protocol-Version`` header get ``400``.

Security (spec requirements for HTTP transports):

* **Origin validation** — browser-originated requests (an ``Origin`` header)
  are rejected unless the origin is allowlisted via
  ``MCP_HTTP_ALLOWED_ORIGINS`` (DNS-rebinding defense).
* **Authorization** — when ``MCP_HTTP_REQUIRE_AUTH`` is on (the default) the
  request must carry credentials accepted by the central
  :class:`~core.auth.manager.AuthManager` (``Authorization: Bearer`` JWT —
  local HS256 or federated OIDC — or an API key). Anonymous results get
  ``401`` with ``WWW-Authenticate: Bearer resource_metadata="…"``, making the
  endpoint an OAuth *resource server* in the sense of the MCP authorization
  spec; the authorization-server side (token issuance, client registration)
  belongs to the deployment's IdP, not this framework.
  The authenticated identity is bound to the request context so tenant-scoped
  tools resolve the correct tenant.
* **Protected-resource metadata** — RFC 9728: an unauthenticated
  ``GET /.well-known/oauth-protected-resource{path}`` (plus the bare
  ``/.well-known/oauth-protected-resource`` alias) publishes this resource's
  identifier and its authorization servers, so a client holding no token can
  discover where to get one. Mounted only when auth is required.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from core.config import get_mcp_config
from core.mcp.errors import MCPProtocolError
from core.mcp.handlers import SUPPORTED_PROTOCOL_VERSIONS
from core.mcp.http_headers import validate_modern_headers
from core.mcp.modern import is_modern, parse_request_meta
from core.mcp.server import MCPServer
from core.observability.logging import get_logger

logger = get_logger(__name__)

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"
# RFC 9728 well-known location for OAuth 2.0 Protected Resource Metadata.
METADATA_PATH = "/.well-known/oauth-protected-resource"


class SessionStore:
    """In-memory MCP session registry with TTL-based expiry.

    Each session is bound to the identity that created it (the authenticated
    ``user_id``, or ``None`` when auth is disabled). ``touch``/``terminate``
    verify the presenting caller owns the session, so one client cannot ride
    another's id (the 2025-06-18 transport requires binding the session id to
    user-specific information). A per-owner cap bounds how many live sessions a
    single identity can hold, so a client cannot mint sessions unbounded and
    pin memory for the whole TTL.

    Process-local by design: Streamable HTTP sessions are an affinity
    contract between one client and one server instance. Deployments running
    multiple replicas need session-affine routing (the spec's recovery path —
    a 404 answered by re-initializing — covers failover).
    """

    def __init__(self, ttl_seconds: float, max_per_owner: int = 0) -> None:
        self._ttl = ttl_seconds
        self._max_per_owner = max_per_owner
        # session_id -> (owner, last_seen)
        self._sessions: dict[str, tuple[str | None, float]] = {}

    def create(self, owner: str | None) -> str | None:
        """Mint a random session id bound to *owner*.

        Returns None when *owner* already holds ``max_per_owner`` live sessions.
        """
        self._prune()
        if self._max_per_owner and self._count_for(owner) >= self._max_per_owner:
            return None
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = (owner, time.monotonic())
        return session_id

    def touch(self, session_id: str, owner: str | None) -> bool:
        """Refresh *session_id*; False when unknown, expired, or not *owner*'s."""
        entry = self._sessions.get(session_id)
        if entry is None:
            return False
        stored_owner, last_seen = entry
        if time.monotonic() - last_seen > self._ttl:
            del self._sessions[session_id]
            return False
        if stored_owner != owner:
            # Belongs to a different identity — refuse (no session takeover).
            return False
        self._sessions[session_id] = (stored_owner, time.monotonic())
        return True

    def terminate(self, session_id: str, owner: str | None) -> bool:
        """Drop *session_id*; False when not active or not *owner*'s."""
        entry = self._sessions.get(session_id)
        if entry is None or entry[0] != owner:
            return False
        del self._sessions[session_id]
        return True

    def _count_for(self, owner: str | None) -> int:
        return sum(1 for stored, _ in self._sessions.values() if stored == owner)

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            s for s, (_, seen) in self._sessions.items() if now - seen > self._ttl
        ]
        for session_id in expired:
            del self._sessions[session_id]


def _jsonrpc_error(
    msg_id: Any, code: int, message: str, status: int, data: Any | None = None
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse(
        status_code=status,
        content={"jsonrpc": "2.0", "id": msg_id, "error": error},
    )


def _origin_rejected(request: Request, allowed_origins: frozenset[str]) -> bool:
    """DNS-rebinding defense: browser origins must be explicitly allowlisted."""
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return origin not in allowed_origins


def _metadata_url(request: Request, path: str) -> str:
    """Absolute URL of this resource's RFC 9728 metadata document."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{METADATA_PATH}{path}"


async def _authenticate(
    request: Request, metadata_url: str
) -> tuple[Any | None, Response | None]:
    """Resolve the caller through the central AuthManager.

    Returns ``(user, None)`` on success or ``(None, 401 response)`` when the
    credentials are missing or resolve to the anonymous identity. The challenge
    carries ``resource_metadata`` (RFC 9728) so a client that has no token yet
    can discover which authorization server to obtain one from.
    """
    from core.auth.manager import get_auth_manager

    user = await get_auth_manager().authenticate(request.headers.get("authorization"))
    if user is None or not getattr(user, "is_authenticated", False):
        return None, JSONResponse(
            status_code=401,
            content={
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32001, "message": "Unauthorized"},
            },
            headers={"WWW-Authenticate": f'Bearer resource_metadata="{metadata_url}"'},
        )
    return user, None


async def _serve_modern(
    server: MCPServer, request: Request, message: dict[str, Any]
) -> Response:
    """Serve one stateless 2026-07-28 request.

    No session is required or minted. The standard headers are validated
    against the body first, then the per-request ``_meta``, because both
    failures carry a status the spec fixes at ``400`` — while the same JSON-RPC
    codes raised later by a handler ride a normal ``200``.
    """
    msg_id = message.get("id")
    try:
        validate_modern_headers(request.headers, message)
        parse_request_meta(message, SUPPORTED_PROTOCOL_VERSIONS)
    except MCPProtocolError as exc:
        return _jsonrpc_error(msg_id, exc.code_for(True), str(exc), 400, data=exc.data)

    response = await server.handle_message(message)
    if response is None:
        return Response(status_code=202)

    # Unknown method is the one handler-level error with its own status: the
    # JSON-RPC body distinguishes it from a 404 served by a host that does not
    # carry an MCP endpoint at all.
    error_code = (response.get("error") or {}).get("code")
    status = 404 if error_code == -32601 else 200
    return JSONResponse(status_code=status, content=response)


def create_mcp_http_router(
    server: MCPServer,
    *,
    config: Any | None = None,
) -> APIRouter:
    """Build the Streamable HTTP router for *server*.

    Args:
        server: The MCP server whose ``handle_message`` serves requests.
        config: Optional :class:`~core.config.mcp.MCPConfig` override
            (defaults to the process config; injectable for tests).

    Returns:
        APIRouter serving POST/DELETE (and a 405 GET) at ``mcp_http_path``.
    """
    cfg = config or get_mcp_config()
    path = cfg.mcp_http_path
    sessions = SessionStore(
        ttl_seconds=float(cfg.mcp_http_session_ttl_seconds),
        max_per_owner=cfg.mcp_http_max_sessions_per_client,
    )
    allowed_origins = cfg.http_allowed_origin_set
    router = APIRouter(tags=["mcp"])

    async def _gate(request: Request) -> tuple[str | None, Response | None]:
        """Shared origin + auth gate.

        Returns ``(owner, rejection)``: ``rejection`` short-circuits the
        handler; otherwise ``owner`` is the session-owner key — the
        authenticated ``user_id``, or ``None`` when auth is disabled.
        """
        if _origin_rejected(request, allowed_origins):
            logger.warning(
                "mcp_http_origin_rejected", origin=request.headers.get("origin")
            )
            return None, _jsonrpc_error(None, -32000, "Origin not allowed", 403)
        if cfg.mcp_http_require_auth:
            user, challenge = await _authenticate(request, _metadata_url(request, path))
            if challenge is not None:
                return None, challenge
            if user is not None:
                # Bind identity so tenant-scoped tools resolve the tenant.
                from core.context import set_user_context

                set_user_context(user.user_id)
                return str(user.user_id), None
        return None, None

    @router.post(path, include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        owner, rejection = await _gate(request)
        if rejection is not None:
            return rejection

        try:
            message = await request.json()
        except Exception:
            return _jsonrpc_error(None, -32700, "Parse error", 400)

        if isinstance(message, list):
            # JSON-RPC batching was removed in the 2025-06-18 revision.
            return _jsonrpc_error(None, -32600, "Batching is not supported", 400)
        if not isinstance(message, dict):
            return _jsonrpc_error(None, -32600, "Invalid request", 400)

        if is_modern(message):
            return await _serve_modern(server, request, message)

        is_initialize = message.get("method") == "initialize"
        headers: dict[str, str] = {}

        if is_initialize:
            new_session = sessions.create(owner)
            if new_session is None:
                return _jsonrpc_error(
                    message.get("id"), -32000, "Session limit exceeded", 429
                )
            headers[SESSION_HEADER] = new_session
        else:
            protocol_version = request.headers.get(PROTOCOL_HEADER)
            if (
                protocol_version is not None
                and protocol_version not in SUPPORTED_PROTOCOL_VERSIONS
            ):
                return _jsonrpc_error(
                    message.get("id"),
                    -32600,
                    f"Unsupported protocol version: {protocol_version}",
                    400,
                )
            session_id = request.headers.get(SESSION_HEADER)
            if not session_id or not sessions.touch(session_id, owner):
                # Spec: 404 tells the client to start a new session (also the
                # response when the id belongs to a different identity).
                return _jsonrpc_error(
                    message.get("id"), -32001, "Session not found", 404
                )

        response = await server.handle_message(message)
        if response is None:
            # Notification (or response-only message): accepted, no body.
            return Response(status_code=202, headers=headers)
        return JSONResponse(status_code=200, content=response, headers=headers)

    @router.delete(path, include_in_schema=False)
    async def mcp_terminate(request: Request) -> Response:
        owner, rejection = await _gate(request)
        if rejection is not None:
            return rejection
        session_id = request.headers.get(SESSION_HEADER)
        if not session_id or not sessions.terminate(session_id, owner):
            return _jsonrpc_error(None, -32001, "Session not found", 404)
        return Response(status_code=204)

    @router.get(path, include_in_schema=False)
    async def mcp_stream_unsupported() -> Response:
        # No server-initiated stream: the spec allows answering GET with 405.
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    if cfg.mcp_http_require_auth:
        # RFC 9728: the metadata document is unauthenticated by design — it is
        # what an unauthenticated client reads to find out where to get a token.
        # Only mounted when the endpoint is actually protected; advertising
        # protection that is not enforced would mislead clients.
        authorization_servers = list(cfg.authorization_server_list)

        def _metadata(request: Request) -> JSONResponse:
            document: dict[str, Any] = {
                "resource": f"{str(request.base_url).rstrip('/')}{path}",
                "bearer_methods_supported": ["header"],
            }
            if authorization_servers:
                document["authorization_servers"] = authorization_servers
            return JSONResponse(document)

        @router.get(f"{METADATA_PATH}{path}", include_in_schema=False)
        async def protected_resource_metadata(request: Request) -> JSONResponse:
            return _metadata(request)

        @router.get(METADATA_PATH, include_in_schema=False)
        async def protected_resource_metadata_root(request: Request) -> JSONResponse:
            # Clients that drop the path component still resolve the document.
            return _metadata(request)

        if not authorization_servers:
            logger.warning(
                "mcp_http_authorization_servers_unset",
                hint="Set MCP_HTTP_AUTHORIZATION_SERVERS or OIDC_ISSUER so "
                "OAuth clients can discover the authorization server.",
            )

    logger.info("mcp_http_transport_ready", path=path)
    return router


__all__ = [
    "PROTOCOL_HEADER",
    "SESSION_HEADER",
    "SessionStore",
    "create_mcp_http_router",
]
