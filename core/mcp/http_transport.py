"""Streamable HTTP transport for the MCP server (spec revision 2025-06-18).

Exposes an :class:`~core.mcp.server.MCPServer` over a single HTTP endpoint,
per the MCP *Streamable HTTP* transport:

* ``POST {path}`` — one JSON-RPC message per request (the 2025-06-18 revision
  removed JSON-RPC batching; arrays are rejected). Requests are answered as
  ``application/json``; notifications get ``202 Accepted`` with no body.
* ``DELETE {path}`` — explicit session termination.
* ``GET {path}`` — ``405``: this server does not offer a server-initiated
  event stream (allowed by the spec).

Sessions follow the spec: ``initialize`` mints an ``Mcp-Session-Id`` echoed
as a response header; every subsequent request must carry it and an unknown
or expired id yields ``404`` (the client then re-initializes). Non-initialize
requests carrying an unsupported ``MCP-Protocol-Version`` header get ``400``.

Security (spec requirements for HTTP transports):

* **Origin validation** — browser-originated requests (an ``Origin`` header)
  are rejected unless the origin is allowlisted via
  ``MCP_HTTP_ALLOWED_ORIGINS`` (DNS-rebinding defense).
* **Authorization** — when ``MCP_HTTP_REQUIRE_AUTH`` is on (the default) the
  request must carry credentials accepted by the central
  :class:`~core.auth.manager.AuthManager` (``Authorization: Bearer`` JWT —
  local HS256 or federated OIDC — or an API key). Anonymous results get
  ``401`` with ``WWW-Authenticate: Bearer``, making the endpoint an OAuth
  *resource server* in the sense of the MCP authorization spec; the
  authorization-server side (token issuance, dynamic client registration)
  belongs to the deployment's IdP, not this framework.
  The authenticated identity is bound to the request context so tenant-scoped
  tools resolve the correct tenant.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from core.config import get_mcp_config
from core.mcp.handlers import SUPPORTED_PROTOCOL_VERSIONS
from core.mcp.server import MCPServer
from core.observability.logging import get_logger

logger = get_logger(__name__)

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"


class SessionStore:
    """In-memory MCP session registry with TTL-based expiry.

    Process-local by design: Streamable HTTP sessions are an affinity
    contract between one client and one server instance. Deployments running
    multiple replicas need session-affine routing (the spec's recovery path —
    a 404 answered by re-initializing — covers failover).
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._sessions: dict[str, float] = {}

    def create(self) -> str:
        """Mint a cryptographically random session id."""
        self._prune()
        session_id = secrets.token_urlsafe(32)
        self._sessions[session_id] = time.monotonic()
        return session_id

    def touch(self, session_id: str) -> bool:
        """Refresh *session_id*; False when unknown or expired."""
        deadline = self._sessions.get(session_id)
        if deadline is None:
            return False
        if time.monotonic() - deadline > self._ttl:
            del self._sessions[session_id]
            return False
        self._sessions[session_id] = time.monotonic()
        return True

    def terminate(self, session_id: str) -> bool:
        """Drop *session_id*; False when it was not active."""
        return self._sessions.pop(session_id, None) is not None

    def _prune(self) -> None:
        now = time.monotonic()
        expired = [s for s, seen in self._sessions.items() if now - seen > self._ttl]
        for session_id in expired:
            del self._sessions[session_id]


def _jsonrpc_error(msg_id: Any, code: int, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        },
    )


def _origin_rejected(request: Request, allowed_origins: frozenset[str]) -> bool:
    """DNS-rebinding defense: browser origins must be explicitly allowlisted."""
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return origin not in allowed_origins


async def _authenticate(request: Request) -> tuple[Any | None, Response | None]:
    """Resolve the caller through the central AuthManager.

    Returns ``(user, None)`` on success or ``(None, 401 response)`` when the
    credentials are missing or resolve to the anonymous identity.
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
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user, None


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
    sessions = SessionStore(ttl_seconds=float(cfg.mcp_http_session_ttl_seconds))
    allowed_origins = cfg.http_allowed_origin_set
    router = APIRouter(tags=["mcp"])

    async def _gate(request: Request) -> Response | None:
        """Shared origin + auth gate. Returns a response to short-circuit."""
        if _origin_rejected(request, allowed_origins):
            logger.warning(
                "mcp_http_origin_rejected", origin=request.headers.get("origin")
            )
            return _jsonrpc_error(None, -32000, "Origin not allowed", 403)
        if cfg.mcp_http_require_auth:
            user, challenge = await _authenticate(request)
            if challenge is not None:
                return challenge
            if user is not None:
                # Bind identity so tenant-scoped tools resolve the tenant.
                from core.context import set_user_context

                set_user_context(user.user_id)
        return None

    @router.post(path, include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        rejection = await _gate(request)
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

        is_initialize = message.get("method") == "initialize"
        headers: dict[str, str] = {}

        if is_initialize:
            headers[SESSION_HEADER] = sessions.create()
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
            if not session_id or not sessions.touch(session_id):
                # Spec: 404 tells the client to start a new session.
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
        rejection = await _gate(request)
        if rejection is not None:
            return rejection
        session_id = request.headers.get(SESSION_HEADER)
        if not session_id or not sessions.terminate(session_id):
            return _jsonrpc_error(None, -32001, "Session not found", 404)
        return Response(status_code=204)

    @router.get(path, include_in_schema=False)
    async def mcp_stream_unsupported() -> Response:
        # No server-initiated stream: the spec allows answering GET with 405.
        return Response(status_code=405, headers={"Allow": "POST, DELETE"})

    logger.info("mcp_http_transport_ready", path=path)
    return router


__all__ = [
    "PROTOCOL_HEADER",
    "SESSION_HEADER",
    "SessionStore",
    "create_mcp_http_router",
]
