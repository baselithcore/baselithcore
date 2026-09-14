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
Those sessions live in Redis whenever the deployment already runs a Redis
cache, and in process memory otherwise — see
:func:`core.mcp.http_sessions.build_session_store`.

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
* **Capability check** — authenticating is not authorizing. The caller must
  also hold ``MCP_HTTP_REQUIRED_SCOPE`` (default ``mcp:invoke``), otherwise
  ``403``. The admin, service, user and job roles carry it by default, so only
  least-privilege scoped keys and read-only guests are newly refused.
* **Audience binding** — RFC 8707: a bearer token whose ``aud`` names a
  different resource than the one published below is refused with ``401``,
  because that token was minted for somebody else. A token carrying no ``aud``
  is refused when ``MCP_REQUIRE_TOKEN_AUDIENCE`` resolves true (production by
  default). API keys are exempt — they are not OAuth tokens.
* **Rate limiting** — each request is metered per identity against
  ``MCP_HTTP_RATE_LIMIT_PER_MINUTE``, so an authenticated caller cannot flood
  the endpoint (every request spawns server-side work).
* **Protected-resource metadata** — RFC 9728: an unauthenticated
  ``GET /.well-known/oauth-protected-resource{path}`` (plus the bare
  ``/.well-known/oauth-protected-resource`` alias) publishes this resource's
  identifier and its authorization servers, so a client holding no token can
  discover where to get one. Mounted only when auth is required.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from core.config import get_mcp_config
from core.mcp.dispatch import RequestDispatcher
from core.mcp.errors import MCPProtocolError
from core.mcp.handlers import SUPPORTED_PROTOCOL_VERSIONS
from core.mcp.http_authz import METADATA_PATH, build_gate, resource_identifier
from core.mcp.http_headers import validate_modern_headers, validate_param_headers
from core.mcp.http_sessions import (
    RedisSessionStore,
    SessionStore,
    build_session_store,
)
from core.mcp.modern import is_modern, parse_request_meta
from core.mcp.progress import progress_context
from core.mcp.server import MCPServer
from core.mcp.sse import SSE_HEADERS, SSE_MEDIA_TYPE, SSEStream, wants_stream
from core.observability.logging import get_logger

logger = get_logger(__name__)

SESSION_HEADER = "Mcp-Session-Id"
PROTOCOL_HEADER = "MCP-Protocol-Version"
# METADATA_PATH (RFC 9728 well-known location) is defined alongside the gate in
# core.mcp.http_authz, and SessionStore alongside its Redis sibling in
# core.mcp.http_sessions; both are re-exported here for existing importers.


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


def _validate_mirrored_params(
    server: MCPServer, request: Request, message: dict[str, Any]
) -> None:
    """Check the tool's ``Mcp-Param-*`` headers against the call arguments.

    A gateway that authorized on a mirrored header while the server executed a
    different body value would be a confused deputy, so any divergence is a
    ``HeaderMismatch`` rather than a silently-preferred source of truth.
    """
    if message.get("method") != "tools/call":
        return
    params = message.get("params") or {}
    tool = server._tools.get(params.get("name", ""))
    if tool is None or not tool.input_schema:
        return
    validate_param_headers(
        request.headers, tool.input_schema, params.get("arguments") or {}
    )


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
        _validate_mirrored_params(server, request, message)
        parse_request_meta(message, SUPPORTED_PROTOCOL_VERSIONS)
    except MCPProtocolError as exc:
        # Deliberate, and not a stack trace: MCPProtocolError instances carry a
        # message this codebase writes, and the JSON-RPC layer of the MCP spec
        # requires a human-readable `message` on every error object.
        # codeql[py/stack-trace-exposure]
        return _jsonrpc_error(msg_id, exc.code_for(True), str(exc), 400, data=exc.data)

    if wants_stream(message):
        return await _serve_stream(server, message)

    response = await _dispatch_once(server, message)
    if response is None:
        return Response(status_code=202)

    # Unknown method is the one handler-level error with its own status: the
    # JSON-RPC body distinguishes it from a 404 served by a host that does not
    # carry an MCP endpoint at all.
    error_code = (response.get("error") or {}).get("code")
    status = 404 if error_code == -32601 else 200
    return JSONResponse(status_code=status, content=response)


async def _dispatch_once(
    server: MCPServer, message: dict[str, Any]
) -> dict[str, Any] | None:
    """Serve one non-streaming request through the shared dispatcher.

    Both HTTP paths used to call ``handle_message`` inline, so HTTP was the one
    transport that did not get what :class:`~core.mcp.dispatch.RequestDispatcher`
    provides: the handler runs as a task (a client that disconnects takes its
    work with it rather than leaving it to finish into nothing) and the
    progress context is established the same way it is on stdio, instead of
    only on the streaming branch.

    The sender is deliberately withheld from the handler: there is no stream on
    this branch, so ``subscriptions/listen`` must keep answering "requires a
    streaming transport" rather than registering a subscription that writes
    into a response that has already been sent.

    The dispatcher nonetheless installs that same sender as the request's
    progress channel, so a handler calling
    :func:`~core.mcp.progress.report_progress` emits a
    ``notifications/progress`` through the very collector the response arrives
    on. Only the **response** — a message carrying no ``method``, correlated to
    this request's id — is returned; notifications are dropped, because a
    single JSON body has nowhere to carry them (a client that wants them asks
    for a stream, which `wants_stream` routes elsewhere).
    """
    collected: list[dict[str, Any]] = []

    async def _collect(outbound: dict[str, Any]) -> None:
        collected.append(outbound)

    async def _handle(
        msg: dict[str, Any], _send: Any | None = None
    ) -> dict[str, Any] | None:
        return await server.handle_message(msg)

    dispatcher = RequestDispatcher(_handle, _collect)
    await dispatcher.dispatch(message)
    await dispatcher.drain()

    msg_id = message.get("id")
    for outbound in collected:
        # A JSON-RPC response has no `method`; a notification does. Match the
        # id too, so a stray correlated-to-nothing message cannot be served as
        # this request's answer.
        if "method" not in outbound and outbound.get("id") == msg_id:
            return outbound
    if collected:
        logger.debug(
            "mcp_http_notifications_dropped",
            count=len(collected),
            hint="Ask for a stream (params._meta.progressToken) to receive them.",
        )
    return None


async def _serve_stream(server: MCPServer, message: dict[str, Any]) -> Response:
    """Answer *message* with an SSE stream scoped to that request.

    The handler runs as a task so its notifications reach the stream while it
    is still working. If the client disconnects, StreamingResponse stops
    consuming and the task is cancelled — closing the stream is the
    cancellation signal on this transport.
    """
    stream = SSEStream()

    async def run() -> None:
        token = progress_context.set((_progress_token(message), stream.send))
        try:
            response = await server.handle_message(message, stream.send)
            if response is not None:
                await stream.send(response)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("mcp_stream_handler_failed", error=str(exc))
        finally:
            progress_context.reset(token)
            await stream.close()

    worker = asyncio.create_task(run())

    async def body() -> Any:
        try:
            async for frame in stream:
                yield frame
        finally:
            # Client gone (or stream finished): never leave the work running.
            worker.cancel()

    return StreamingResponse(
        body(), media_type=SSE_MEDIA_TYPE, headers=dict(SSE_HEADERS)
    )


def _progress_token(message: dict[str, Any]) -> Any:
    meta = (message.get("params") or {}).get("_meta") or {}
    return meta.get("progressToken")


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
    sessions: SessionStore | RedisSessionStore = build_session_store(cfg)
    allowed_origins = cfg.http_allowed_origin_set
    router = APIRouter(tags=["mcp"])

    _gate = build_gate(cfg, path, allowed_origins)

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
            new_session = await sessions.create(owner)
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
            if not session_id or not await sessions.touch(session_id, owner):
                # Spec: 404 tells the client to start a new session (also the
                # response when the id belongs to a different identity).
                return _jsonrpc_error(
                    message.get("id"), -32001, "Session not found", 404
                )

        response = await _dispatch_once(server, message)
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
        if not session_id or not await sessions.terminate(session_id, owner):
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
                # The same value the gate validates a token's `aud` against —
                # one function, so the advertised resource and the enforced
                # one cannot drift apart.
                "resource": resource_identifier(request, path, cfg),
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
    "RedisSessionStore",
    "SessionStore",
    "build_session_store",
    "create_mcp_http_router",
]
