"""SSRF-hardened httpx client factory.

``create_hardened_async_client`` wraps the (real or mock) transport in
:class:`SsrfBlockingTransport`, which re-validates and IP-pins every request
that reaches the wire — including every redirect hop that httpx follows —
so no call site can forget the guard.
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any

import httpx

from core.security.ssrf import SsrfPolicy, resolve_pinned_target

_DEFAULT_POLICY = SsrfPolicy()


class SsrfBlockingTransport(httpx.AsyncBaseTransport):
    """Transport wrapper: validate + pin each request before delegating."""

    def __init__(self, inner: httpx.AsyncBaseTransport, policy: SsrfPolicy) -> None:
        self._inner = inner
        self._policy = policy

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        pinned_url, host = await asyncio.to_thread(
            resolve_pinned_target, str(request.url), self._policy
        )
        if pinned_url != str(request.url):
            # Do not mutate request in place; relative redirects are joined
            # against request.url (see httpx._models.Response._parse_redirect_url).
            # Create a separate request for the transport with pinned URL.
            headers = httpx.Headers(request.headers)
            headers["Host"] = host
            extensions = {**request.extensions, "sni_hostname": host}
            pinned_request = httpx.Request(
                request.method,
                pinned_url,
                headers=headers,
                stream=request.stream,
                extensions=extensions,
            )
            return await self._inner.handle_async_request(pinned_request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> SsrfBlockingTransport:
        await self._inner.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc_value, traceback)


def create_hardened_async_client(
    policy: SsrfPolicy | None = None, **httpx_kwargs: Any
) -> httpx.AsyncClient:
    """Build an ``httpx.AsyncClient`` whose every request passes the SSRF guard.

    Args:
        policy: Egress policy; defaults to the strict :class:`SsrfPolicy`.
        **httpx_kwargs: Forwarded to ``httpx.AsyncClient``. A ``transport``
            kwarg, if given (e.g. a ``MockTransport`` in tests), is wrapped
            rather than replaced.

    Raises:
        ValueError: If ``mounts``, ``proxy``, or ``proxies`` kwargs are provided;
            these would bypass the SSRF guard. Configure proxies on the inner
            transport instead.

    Note:
        By default, keep-alive is disabled (``max_keepalive_connections=0``) to
        prevent connection coalescing between different validated hostnames that
        may share a pinned IP address. Pass explicit ``limits`` to override.
    """
    # FINDING 1: Reject mounts/proxy/proxies to prevent bypass
    if "mounts" in httpx_kwargs or "proxy" in httpx_kwargs or "proxies" in httpx_kwargs:
        raise ValueError(
            "mounts/proxy/proxies kwargs would bypass the SSRF guard; "
            "configure the proxy on the inner transport instead"
        )

    transport_kwarg = httpx_kwargs.pop("transport", None)

    # FINDING 3: Disable keep-alive by default to prevent connection coalescing
    # between different validated hostnames sharing a pinned IP.
    if transport_kwarg is None:
        if "limits" not in httpx_kwargs:
            httpx_kwargs["limits"] = httpx.Limits(max_keepalive_connections=0)
        inner = httpx.AsyncHTTPTransport()
    else:
        inner = transport_kwarg

    return httpx.AsyncClient(
        transport=SsrfBlockingTransport(inner, policy or _DEFAULT_POLICY),
        **httpx_kwargs,
    )
