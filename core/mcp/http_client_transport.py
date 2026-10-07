"""Streamable HTTP client transport for :class:`~core.mcp.client.MCPClient`.

Speaks the MCP *Streamable HTTP* transport against a remote server URL, in
either protocol era:

* JSON-RPC messages are POSTed one per request; responses arrive as
  ``application/json`` or as a ``text/event-stream`` drained until the reply to
  the request appears.
* The ``Mcp-Session-Id`` header minted by the server's ``initialize``
  response is echoed on every subsequent request; ``close()`` terminates the
  session with a DELETE.
* The negotiated ``MCP-Protocol-Version`` is sent on post-initialize
  requests, per spec.
* Modern (2026-07-28) messages additionally carry the standard ``Mcp-Method``
  and ``Mcp-Name`` headers derived from the body, Base64-encoded through the
  ``=?base64?...?=`` sentinel when the value is not header-safe.
* Authorization is caller-supplied: static ``headers``, and/or an ``auth``
  provider evaluated for **every** request (token refresh), and/or an
  ``on_unauthorized`` hook that may supply fresh headers for one retry after a
  401. Credentials are never logged and never placed in an error message.

The server is untrusted, so the transport bounds everything it controls:
``max_response_bytes`` caps a JSON body and the accumulated SSE stream, a
``total_timeout`` deadline covers the whole request including the stream
(``httpx`` timeouts only bound each phase, which a keep-alive trickle defeats),
redirects are never followed, ``allowed_hosts`` pins the endpoint, and the
SSRF guard screens every connection.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from core.config import get_mcp_config
from core.mcp.bounded_http import (
    IDENTITY_ENCODING,
    SSEParser,
    check_response_headers,
    read_capped,
    too_large,
)
from core.mcp.client_errors import (
    MCPAuthError,
    MCPBlockedError,
    MCPProtocolError,
    MCPTransportError,
)
from core.mcp.client_limits import client_limits
from core.mcp.http_headers import extract_param_headers, standard_headers
from core.mcp.modern import PROTOCOL_VERSION_KEY
from core.mcp.reply_matching import is_notification, is_reply, is_server_request
from core.observability.logging import get_logger
from core.security.http import create_hardened_async_client
from core.security.ssrf import SsrfError, SsrfPolicy

logger = get_logger(__name__)

_SESSION_HEADER = "Mcp-Session-Id"
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_ACCEPT = "application/json, text/event-stream"

HeaderProvider = Callable[[], Awaitable[Mapping[str, str]]]
UnauthorizedHook = Callable[[MCPAuthError], Awaitable[Mapping[str, str] | None]]
ServerRequestHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
NotificationHandler = Callable[[dict[str, Any]], None]


class HTTPClientTransport:
    """One MCP session over Streamable HTTP."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        httpx_transport: httpx.AsyncBaseTransport | None = None,
        max_response_bytes: int | None = None,
        total_timeout: float | None = None,
        allowed_hosts: Iterable[str] | None = None,
        auth: HeaderProvider | None = None,
        on_unauthorized: UnauthorizedHook | None = None,
    ) -> None:
        """
        Args:
            url: The server's single MCP endpoint (e.g. ``https://host/mcp``).
            headers: Static headers added to every request.
            timeout: Per-phase network timeout; defaults to the MCP client
                request timeout.
            httpx_transport: Optional httpx transport override (in-process
                testing).
            max_response_bytes: Cap on one response body, JSON or accumulated
                SSE. Defaults to ``MCP_CLIENT_MAX_RESPONSE_BYTES``.
            total_timeout: Wall-clock deadline for one request, including a
                whole SSE stream. Defaults to
                ``MCP_CLIENT_TOTAL_DEADLINE_SECONDS``.
            allowed_hosts: Exact hostnames the endpoint may have. When given,
                the URL's host must be among them (``ValueError`` otherwise)
                and the SSRF policy enforces the same list on every request.
            auth: Async callable returning headers for *each* request.
            on_unauthorized: Async hook called once per request after a 401;
                returns fresh headers to retry with, or ``None`` to give up.

        Raises:
            ValueError: ``allowed_hosts`` is given and the URL host is not in it.
        """
        self.url = url
        self._headers = dict(headers or {})
        self._auth = auth
        self._on_unauthorized = on_unauthorized
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        # Input schemas of the server's tools, refreshed from tools/list, so a
        # tools/call can mirror its `x-mcp-header` parameters into headers.
        self.tool_schemas: dict[str, Any] = {}
        limits = client_limits()
        self._max_bytes = max_response_bytes or limits.max_response_bytes
        self._total_timeout = total_timeout or limits.total_deadline
        hosts = frozenset(allowed_hosts) if allowed_hosts is not None else None
        host = urlparse(url).hostname
        if hosts is not None and host not in hosts:
            raise SsrfError(
                f"MCP endpoint host {host!r} is not in the allowed host list"
            )
        mcp_config = get_mcp_config()
        self._client = create_hardened_async_client(
            policy=SsrfPolicy(
                allow_internal=mcp_config.mcp_allow_internal_endpoints,
                allowed_hosts=hosts,
            ),
            timeout=timeout or mcp_config.mcp_client_request_timeout,
            transport=httpx_transport,
            follow_redirects=False,
        )

    # ------------------------------------------------------------------ headers

    async def _request_headers(
        self,
        message: dict[str, Any] | None = None,
        override: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        base = dict(self._headers)
        if self._auth is not None:
            base.update(await self._auth())
        if override:
            base.update(override)
        headers = {
            "Accept": _ACCEPT,
            **{k: v for k, v in base.items() if k.lower() != "accept-encoding"},
            # A compressed reply is refused (core.mcp.bounded_http); do not
            # invite one, whatever the caller's static headers say.
            "Accept-Encoding": IDENTITY_ENCODING,
        }
        if self._session_id:
            headers[_SESSION_HEADER] = self._session_id
        if self._protocol_version:
            headers[_PROTOCOL_HEADER] = self._protocol_version
        if message is not None:
            headers.update(standard_headers(message))
            headers.update(self._param_headers(message))
        return headers

    def _param_headers(self, message: dict[str, Any]) -> dict[str, str]:
        """`Mcp-Param-*` headers for a tools/call whose tool annotates them."""
        params = message.get("params") or {}
        if message.get("method") != "tools/call" or PROTOCOL_VERSION_KEY not in (
            params.get("_meta") or {}
        ):
            return {}
        schema = self.tool_schemas.get(params.get("name", ""))
        if not schema:
            return {}
        return extract_param_headers(schema, params.get("arguments") or {})

    # --------------------------------------------------------------------- send

    async def send(
        self,
        message: dict[str, Any],
        *,
        on_server_request: ServerRequestHandler | None = None,
        on_notification: NotificationHandler | None = None,
    ) -> dict[str, Any] | None:
        """POST one JSON-RPC message; return the reply object (or None).

        None is returned for accepted notifications (HTTP 202 / empty body).
        Server requests met while draining the reply are answered through
        *on_server_request* (a POST of the response); notifications go to
        *on_notification*.

        Raises:
            MCPAuthError: 401/403 (after the single ``on_unauthorized`` retry).
            MCPTransportError: any other HTTP, network, deadline, size or
                SSRF failure. No response body is ever put in the message.
            MCPProtocolError: the response held no reply to the request.
        """
        try:
            async with asyncio.timeout(self._total_timeout):
                return await self._send_with_retry(
                    message, on_server_request, on_notification
                )
        except TimeoutError as exc:
            raise MCPTransportError(
                f"MCP request exceeded the {self._total_timeout:g}s total deadline"
            ) from exc
        except SsrfError as exc:
            raise MCPBlockedError(
                f"MCP request blocked by egress policy: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise MCPTransportError("MCP request timed out") from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(
                f"MCP HTTP transport failure ({type(exc).__name__})"
            ) from exc

    async def _send_with_retry(
        self,
        message: dict[str, Any],
        on_server_request: ServerRequestHandler | None,
        on_notification: NotificationHandler | None,
    ) -> dict[str, Any] | None:
        override: Mapping[str, str] | None = None
        for attempt in (0, 1):
            headers = await self._request_headers(message, override)
            try:
                return await self._post_once(
                    message, headers, on_server_request, on_notification
                )
            except MCPAuthError as err:
                if err.status_code != 401 or attempt or self._on_unauthorized is None:
                    raise
                fresh = await self._on_unauthorized(err)
                if not fresh:
                    raise
                override = fresh
        raise AssertionError("unreachable")  # pragma: no cover

    async def _post_once(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
        on_server_request: ServerRequestHandler | None,
        on_notification: NotificationHandler | None,
    ) -> dict[str, Any] | None:
        async with self._client.stream(
            "POST", self.url, json=message, headers=headers
        ) as response:
            # A legacy server mints its session on the initialize response;
            # capture it wherever it appears so later requests can echo it.
            self.capture_session(response.headers)
            status = response.status_code
            if 300 <= status < 400:
                raise MCPTransportError(
                    f"MCP server answered with a redirect (HTTP {status}); "
                    "redirects are never followed",
                    status_code=status,
                )
            if status in (401, 403):
                raise MCPAuthError.from_response(
                    status, response.headers.get("www-authenticate")
                )
            if status >= 400:
                raise MCPTransportError(
                    f"MCP HTTP transport error {status}", status_code=status
                )
            request_id = message.get("id")
            if status == 202 or request_id is None:
                return None
            check_response_headers(response, self._max_bytes)

            if response.headers.get("content-type", "").startswith("text/event-stream"):
                reply = await self._drain_sse(
                    response, request_id, on_server_request, on_notification
                )
            else:
                body = await read_capped(response, self._max_bytes)
                if not body.strip():
                    return None
                reply = await self._reply_from_json(
                    body, request_id, on_server_request, on_notification
                )

        if message.get("method") == "initialize":
            # Record the negotiated legacy version for the protocol header.
            negotiated = (reply.get("result") or {}).get("protocolVersion")
            if negotiated:
                self._protocol_version = str(negotiated)
        return reply

    # ---------------------------------------------------------------- responses

    async def _dispatch(
        self,
        event: Any,
        request_id: Any,
        on_server_request: ServerRequestHandler | None,
        on_notification: NotificationHandler | None,
    ) -> dict[str, Any] | None:
        """Classify one decoded message; return it only when it is our reply."""
        if not isinstance(event, dict):
            return None
        if is_reply(event, request_id):
            return event
        if is_server_request(event):
            if on_server_request is not None:
                await self._answer(await on_server_request(event))
        elif is_notification(event) and on_notification is not None:
            on_notification(event)
        return None

    async def _reply_from_json(
        self,
        body: bytes,
        request_id: Any,
        on_server_request: ServerRequestHandler | None,
        on_notification: NotificationHandler | None,
    ) -> dict[str, Any]:
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise MCPProtocolError("MCP response is not valid JSON") from exc
        for event in payload if isinstance(payload, list) else [payload]:
            reply = await self._dispatch(
                event, request_id, on_server_request, on_notification
            )
            if reply is not None:
                return reply
        raise MCPProtocolError("MCP response did not contain a reply to the request")

    async def _drain_sse(
        self,
        response: httpx.Response,
        request_id: Any,
        on_server_request: ServerRequestHandler | None,
        on_notification: NotificationHandler | None,
    ) -> dict[str, Any]:
        parser = SSEParser()
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                raise too_large(self._max_bytes)
            for data in parser.feed(chunk):
                try:
                    event = json.loads(data)
                except ValueError:
                    continue
                reply = await self._dispatch(
                    event, request_id, on_server_request, on_notification
                )
                if reply is not None:
                    return reply
        raise MCPProtocolError("MCP event stream ended without a reply to the request")

    async def _answer(self, response: dict[str, Any]) -> None:
        """POST our response to a server request (best effort)."""
        try:
            headers = await self._request_headers()
            async with self._client.stream(
                "POST", self.url, json=response, headers=headers
            ) as posted:
                if posted.status_code >= 400:
                    logger.debug(
                        "mcp_server_request_answer_rejected", status=posted.status_code
                    )
        except MCPAuthError:
            raise
        except (httpx.HTTPError, SsrfError) as exc:
            logger.debug("mcp_server_request_answer_failed", error=type(exc).__name__)

    def capture_session(self, response_headers: dict[str, str] | Any) -> None:
        """Record the session id minted by the initialize response."""
        session_id = response_headers.get(_SESSION_HEADER) or response_headers.get(
            _SESSION_HEADER.lower()
        )
        if session_id:
            self._session_id = session_id

    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the initialize handshake; returns the JSON-RPC ``result``."""
        reply = await self.send(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params}
        )
        if reply is None:
            raise MCPProtocolError("Empty MCP response for 'initialize'")
        if "error" in reply:
            error = reply["error"]
            raise MCPProtocolError(
                f"MCP error {error.get('code')}: {error.get('message')}"
            )
        result: dict[str, Any] = reply.get("result", {})
        return result

    async def close(self) -> None:
        """Terminate the session (best-effort DELETE) and release the pool."""
        try:
            if self._session_id:
                async with asyncio.timeout(self._total_timeout):
                    await self._client.delete(
                        self.url, headers=await self._request_headers()
                    )
        except Exception as exc:  # best-effort: session expiry handles the rest
            logger.debug("mcp_http_session_delete_failed", error=type(exc).__name__)
        finally:
            self._session_id = None
            await self._client.aclose()


__all__ = ["HTTPClientTransport"]
