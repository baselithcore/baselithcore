"""Streamable HTTP client transport for :class:`~core.mcp.client.MCPClient`.

Speaks the MCP *Streamable HTTP* transport (spec revision 2025-06-18) against
a remote server URL:

* JSON-RPC messages are POSTed one per request; responses arrive as
  ``application/json`` or as a ``text/event-stream`` the transport drains
  until the reply matching the request id appears.
* The ``Mcp-Session-Id`` header minted by the server's ``initialize``
  response is echoed on every subsequent request; ``close()`` terminates the
  session with a DELETE.
* The negotiated ``MCP-Protocol-Version`` is sent on post-initialize
  requests, per spec.
* Authorization is caller-supplied via static headers (e.g.
  ``{"Authorization": "Bearer <token>"}``) — token acquisition belongs to the
  deployment's IdP flow, not this transport.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from core.config import get_mcp_config
from core.observability.logging import get_logger

logger = get_logger(__name__)

_SESSION_HEADER = "Mcp-Session-Id"
_PROTOCOL_HEADER = "MCP-Protocol-Version"
_ACCEPT = "application/json, text/event-stream"


class HTTPClientTransport:
    """One MCP session over Streamable HTTP."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        httpx_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """
        Args:
            url: The server's single MCP endpoint (e.g. ``https://host/mcp``).
            headers: Static headers added to every request (authorization).
            timeout: Per-request timeout; defaults to the MCP client timeout.
            httpx_transport: Optional httpx transport override (in-process
                ASGI testing).
        """
        self.url = url
        self._headers = dict(headers or {})
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._client = httpx.AsyncClient(
            timeout=timeout or get_mcp_config().mcp_client_request_timeout,
            transport=httpx_transport,
        )

    def _request_headers(self) -> dict[str, str]:
        headers = {"Accept": _ACCEPT, **self._headers}
        if self._session_id:
            headers[_SESSION_HEADER] = self._session_id
        if self._protocol_version:
            headers[_PROTOCOL_HEADER] = self._protocol_version
        return headers

    async def send(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """POST one JSON-RPC message; return the response object (or None).

        None is returned for accepted notifications (HTTP 202 / empty body).
        Raises ``RuntimeError`` on transport-level failures.
        """
        response = await self._client.post(
            self.url, json=message, headers=self._request_headers()
        )
        if response.status_code == 202 or not response.content:
            return None
        if response.status_code >= 400:
            # Error bodies are JSON-RPC error envelopes when available.
            detail: Any
            try:
                detail = response.json()
            except Exception:
                detail = response.text
            raise RuntimeError(
                f"MCP HTTP transport error {response.status_code}: {detail}"
            )

        content_type = response.headers.get("content-type", "")
        if content_type.startswith("text/event-stream"):
            return self._parse_sse(response.text, message.get("id"))
        return response.json()

    @staticmethod
    def _parse_sse(body: str, request_id: Any) -> dict[str, Any] | None:
        """Drain an SSE body and return the reply matching *request_id*.

        Minimal parser: concatenates ``data:`` lines per event, JSON-decodes
        each event, and returns the first JSON-RPC response whose ``id``
        matches (or the last decoded object when the server doesn't echo
        ids on a single-response stream).
        """
        last: dict[str, Any] | None = None
        for raw_event in body.replace("\r\n", "\n").split("\n\n"):
            data_lines = [
                line[len("data:") :].strip()
                for line in raw_event.split("\n")
                if line.startswith("data:")
            ]
            if not data_lines:
                continue
            try:
                event = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                if request_id is not None and event.get("id") == request_id:
                    return event
                last = event
        return last

    def capture_session(self, response_headers: dict[str, str] | Any) -> None:
        """Record the session id minted by the initialize response."""
        session_id = response_headers.get(_SESSION_HEADER) or response_headers.get(
            _SESSION_HEADER.lower()
        )
        if session_id:
            self._session_id = session_id

    async def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Run the initialize handshake; returns the JSON-RPC ``result``."""
        response = await self._client.post(
            self.url,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": params},
            headers={"Accept": _ACCEPT, **self._headers},
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"MCP HTTP initialize failed ({response.status_code}): "
                f"{response.text[:500]}"
            )
        self.capture_session(response.headers)
        payload = response.json()
        if "error" in payload:
            error = payload["error"]
            raise RuntimeError(f"MCP error {error.get('code')}: {error.get('message')}")
        result = payload.get("result", {})
        negotiated = result.get("protocolVersion")
        if negotiated:
            self._protocol_version = str(negotiated)
        return result

    async def close(self) -> None:
        """Terminate the session (best-effort DELETE) and release the pool."""
        try:
            if self._session_id:
                await self._client.delete(self.url, headers=self._request_headers())
        except Exception as exc:  # best-effort: session expiry handles the rest
            logger.debug("mcp_http_session_delete_failed", error=str(exc))
        finally:
            self._session_id = None
            await self._client.aclose()


__all__ = ["HTTPClientTransport"]
