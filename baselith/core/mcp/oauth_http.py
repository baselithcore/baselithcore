"""Bounded, SSRF-guarded HTTP for the OAuth client.

One class so :mod:`baselith.core.mcp.oauth_client` stays about OAuth: deadline, response
cap, no redirects, ``https`` required (loopback excepted under
``MCP_ALLOW_INTERNAL_ENDPOINTS``), typed errors with no raw bodies.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import urlparse

import httpx

from baselith.core.config import get_mcp_config
from baselith.core.mcp.bounded_http import IDENTITY_ENCODING, read_capped
from baselith.core.mcp.client_errors import MCPBlockedError, MCPTransportError, bounded
from baselith.core.mcp.oauth_types import MCPOAuthError
from baselith.core.security.http import create_hardened_async_client
from baselith.core.security.ssrf import (
    SsrfError,
    SsrfPolicy,
    hostname_is_blocked_literal,
)


class OAuthHTTP:
    """Every network call the OAuth client makes goes through here."""

    def __init__(
        self,
        *,
        httpx_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float | None = None,
        max_response_bytes: int | None = None,
        allowed_hosts: Iterable[str] | None = None,
    ) -> None:
        config = get_mcp_config()
        self._timeout = timeout or config.mcp_oauth_timeout_seconds
        self._max_bytes = max_response_bytes or config.mcp_oauth_max_response_bytes
        self._allow_internal = config.mcp_allow_internal_endpoints
        self._client = create_hardened_async_client(
            policy=SsrfPolicy(
                allow_internal=self._allow_internal,
                allowed_hosts=frozenset(allowed_hosts) if allowed_hosts else None,
            ),
            timeout=self._timeout,
            transport=httpx_transport,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Release the HTTP connection pool."""
        await self._client.aclose()

    def require_https(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.hostname:
            return
        loopback_dev = (
            self._allow_internal
            and parsed.scheme == "http"
            and parsed.hostname is not None
            and hostname_is_blocked_literal(parsed.hostname)
        )
        if not loopback_dev:
            raise MCPOAuthError(f"OAuth endpoint must use https: {bounded(url, 120)}")

    async def request(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """One bounded request: deadline, size cap, no redirects, SSRF-guarded."""
        self.require_https(url)
        try:
            async with asyncio.timeout(self._timeout):
                async with self._client.stream(
                    method,
                    url,
                    data=data,
                    json=json_body,
                    headers={
                        "Accept": "application/json",
                        **(headers or {}),
                        "Accept-Encoding": IDENTITY_ENCODING,
                    },
                ) as response:
                    status = response.status_code
                    if 300 <= status < 400:
                        raise MCPTransportError(
                            f"OAuth server answered with a redirect (HTTP {status}); "
                            "redirects are never followed",
                            status_code=status,
                        )
                    return status, await read_capped(response, self._max_bytes)
        except TimeoutError as exc:
            raise MCPTransportError(
                f"OAuth request exceeded the {self._timeout:g}s deadline"
            ) from exc
        except SsrfError as exc:
            raise MCPBlockedError(
                f"OAuth request blocked by egress policy: {exc}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise MCPTransportError("OAuth request timed out") from exc
        except httpx.HTTPError as exc:
            raise MCPTransportError(
                f"OAuth HTTP failure ({type(exc).__name__})"
            ) from exc

    @staticmethod
    def parse_object(body: bytes, what: str) -> dict[str, Any]:
        try:
            parsed = json.loads(body)
        except ValueError as exc:
            raise MCPOAuthError(f"{what} is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise MCPOAuthError(f"{what} is not a JSON object")
        return parsed

    async def fetch_metadata(self, urls: Sequence[str], what: str) -> dict[str, Any]:
        """First candidate URL that answers 200 with a JSON object."""
        last_status: int | None = None
        for url in dict.fromkeys(urls):
            status, body = await self.request("GET", url)
            if status == 200:
                return self.parse_object(body, what)
            last_status = status
        raise MCPOAuthError(
            f"{what} not found (last HTTP status {last_status})"
            if last_status is not None
            else f"{what} not found"
        )


__all__ = ["OAuthHTTP"]
