"""``ConnectorHttp``: the one outbound HTTP path every connector shares.

It composes pieces core already owns instead of re-implementing them:

* ``create_hardened_async_client`` + ``SsrfPolicy(allowed_hosts=...)`` so no
  request, redirect hop included, reaches a host the spec did not declare or
  an internal address;
* ``core.resilience.retry`` around a named ``CircuitBreaker``
  (``connector.<name>``), with ``Retry-After`` honoured through the
  ``retry_after`` attribute of :class:`ConnectorRateLimitedError`;
* the error hierarchy of :mod:`core.connectors.errors`, with credentials
  redacted out of every message.

Only a genuine outage (transport failure, timeout, 408, 5xx) counts against
the breaker. A 404, a rejected credential or a rate limit says nothing about
the provider being down, so none of them can open the circuit.

Retries respect idempotency. A request whose method is idempotent (RFC 9110
§9.2.2) is retried on any transient failure. Any other request (a POST that
opens a ticket, a charge) is retried only when the server provably did not
act on it: a 429, or a connection that was never established. A 5xx or a
read timeout after a POST may mean the side effect already happened, and
repeating it would double it; the caller gets the error instead, or declares
the call safe with ``idempotent=True``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

import httpx

from core.connectors.errors import (
    ConnectorConnectError,
    ConnectorEgressError,
    ConnectorError,
    ConnectorRateLimitedError,
    ConnectorResponseTooLargeError,
    ConnectorTransientError,
    ConnectorUnavailableError,
    error_for_response,
    redact,
)
from core.connectors.types import ConnectorSpec
from core.resilience import CircuitBreakerError, get_circuit_breaker, retry
from core.security.http import SsrfBlockingTransport, create_hardened_async_client
from core.security.ssrf import SsrfError, SsrfPolicy

IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"})

# Transport failures raised before a single byte of the request was sent.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class ConnectorHttp:
    """SSRF-guarded, retrying, circuit-broken HTTP client for one connector.

    Args:
        spec: The connector's spec (name, egress allowlist, resilience knobs).
        secrets: Credential values to redact from error messages.
        client: An injected client, never closed here. It must come from
            ``create_hardened_async_client`` so the SSRF guard still applies;
            its own policy then replaces ``spec.allowed_hosts``.
        transport: Inner transport for the owned client (tests pass an
            ``httpx.MockTransport``); still wrapped by the SSRF guard.
    """

    def __init__(
        self,
        spec: ConnectorSpec,
        *,
        secrets: Iterable[str] = (),
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if client is not None and not isinstance(
            getattr(client, "_transport", None), SsrfBlockingTransport
        ):
            raise ValueError(
                f"connector {spec.name!r}: an injected client must be built with "
                "create_hardened_async_client, or it would bypass the SSRF guard"
            )
        self._spec = spec
        self._secrets = tuple(s for s in secrets if s)
        self._client = client
        self._owns_client = client is None
        self._transport = transport

    @property
    def client(self) -> httpx.AsyncClient:
        """The underlying client, created on first use when owned."""
        if self._client is None:
            kwargs: dict[str, Any] = {"timeout": self._spec.timeout_s}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = create_hardened_async_client(
                SsrfPolicy(allowed_hosts=self._spec.allowed_hosts), **kwargs
            )
        return self._client

    async def request(
        self,
        method: str,
        url: str,
        *,
        idempotent: bool | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """Send a request and return the successful response.

        Args:
            method: HTTP method.
            url: Absolute URL; its host must be in ``spec.allowed_hosts``.
            idempotent: Whether repeating the request is harmless. Defaults
                to what the method implies; pass ``True`` for e.g. a POST
                search endpoint, or an API taking an idempotency key.
            **kwargs: Forwarded to ``httpx.AsyncClient.request``.

        Raises:
            ConnectorError: A subclass describing why the call failed; see
                :mod:`core.connectors.errors`.
        """
        spec = self._spec
        breaker = get_circuit_breaker(f"connector.{spec.name}")
        if idempotent is None:
            idempotent = method.upper() in IDEMPOTENT_METHODS
        retryable: tuple[type[Exception], ...] = (
            (ConnectorTransientError,)
            if idempotent
            else (ConnectorRateLimitedError, ConnectorConnectError)
        )

        @retry(
            max_attempts=spec.max_attempts,
            base_delay=spec.retry_base_delay,
            max_delay=spec.retry_max_delay,
            retryable_exceptions=retryable,
        )
        async def attempt() -> httpx.Response:
            try:
                outcome = await breaker.async_call(self._send, method, url, **kwargs)
            except CircuitBreakerError as exc:
                raise ConnectorUnavailableError(
                    spec.name, "circuit open; call not attempted"
                ) from exc
            if isinstance(outcome, ConnectorError):
                raise outcome
            return outcome

        response: httpx.Response = await attempt()
        return response

    async def _send(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response | ConnectorError:
        """One wire attempt, run inside the breaker.

        Raises only for an outage, so only an outage is counted. Every other
        failure is *returned* and raised by the caller outside the breaker.
        """
        name = self._spec.name
        try:
            async with asyncio.timeout(self._spec.deadline_s):
                response = await self._read_capped(method, url, **kwargs)
        except TimeoutError as exc:
            raise ConnectorTransientError(
                name,
                f"no complete response within the {self._spec.deadline_s}s deadline",
            ) from exc
        except SsrfError as exc:
            return ConnectorEgressError(name, redact(str(exc), self._secrets))
        except _NOT_SENT as exc:
            detail = redact(str(exc).strip() or type(exc).__name__, self._secrets)
            raise ConnectorConnectError(name, detail) from exc
        except httpx.TransportError as exc:
            detail = redact(str(exc).strip() or type(exc).__name__, self._secrets)
            raise ConnectorTransientError(name, detail) from exc
        if isinstance(response, ConnectorError):
            return response
        error = error_for_response(name, response, self._secrets)
        if error is None:
            return response
        if type(error) is ConnectorTransientError:
            raise error
        return error

    async def _read_capped(
        self, method: str, url: str, **kwargs: Any
    ) -> httpx.Response | ConnectorResponseTooLargeError:
        """Send and read the body through the cap; never buffers past it.

        The body is streamed and assembled into a plain buffered
        ``httpx.Response`` the callers can use as usual; ``Content-Encoding``
        and ``Content-Length`` are dropped from the copy because the chunks
        were decoded on the way in.
        """
        cap = self._spec.max_response_bytes
        too_large = ConnectorResponseTooLargeError(
            self._spec.name, f"response body exceeds {cap} bytes"
        )
        request = self.client.build_request(method, url, **kwargs)
        response = await self.client.send(request, stream=True)
        try:
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > cap:
                return too_large
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > cap:
                    return too_large
        finally:
            await response.aclose()
        headers = [
            (k, v)
            for k, v in response.headers.multi_items()
            if k.lower() not in ("content-encoding", "content-length")
        ]
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=bytes(body),
            request=request,
        )

    async def aclose(self) -> None:
        """Close the client if this instance created it."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["IDEMPOTENT_METHODS", "ConnectorHttp"]
