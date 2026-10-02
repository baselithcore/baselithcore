"""Shared HTTP client with retry/backoff for the TEI-backed services."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from core.observability.logging import get_logger
from core.services.inference.errors import InferenceConfigError, InferenceError

logger = get_logger(__name__)

#: Statuses worth a second attempt: the request was fine, the server was not.
_RETRYABLE_STATUS = frozenset({408, 425, 500, 502, 503, 504})
#: Rate limiting is retried only on request (``retry_rate_limited``): a TEI
#: answers 429 when its queue is full, and hammering a full queue from the
#: interactive path only makes the backlog longer.
_RATE_LIMITED = 429

_LOOPBACK_NAMES = frozenset({"localhost", "localhost.localdomain"})
#: DNS suffixes that only resolve inside a Kubernetes cluster.
_CLUSTER_SUFFIXES = (".svc", ".svc.cluster.local", ".cluster.local")


def url_may_carry_key(url: str) -> bool:
    """Whether a bearer token may travel to ``url`` over the wire it names.

    ``https`` always. Plain ``http`` only to a host that cannot be on the
    public internet: loopback, a single-label name (a compose service or a
    same-namespace Kubernetes Service, ``http://tei-embed:8080``), or a
    cluster-local DNS name (``<svc>.<ns>.svc``, ``...svc.cluster.local``).
    Anything else — a dotted name, a public address — is refused unless the
    operator sets ``allow_insecure_key``.
    """
    parts = urlsplit(url)
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return False
    if host in _LOOPBACK_NAMES or "." not in host:
        return True
    if host.endswith(_CLUSTER_SUFFIXES):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class RemoteClient:
    """One shared ``httpx.AsyncClient`` plus a retrying ``post_json``.

    Retries on timeouts, connection errors and 5xx with exponential backoff
    until ``max_retries`` or the total time budget (``max_total_seconds``) is
    spent, whichever comes first; any other 4xx fails immediately (retrying a
    malformed request cannot succeed). Every attempt's timeout is clamped to
    the remaining budget, so the whole call stays under the edge proxy's read
    timeout instead of outliving it and surfacing as a 504 to the user.

    A bearer token is sent only over https or to a host that is not on the
    public internet (loopback, a compose/cluster-internal name — see
    :func:`url_may_carry_key`), unless ``allow_insecure_key`` says otherwise;
    environment proxies are ignored so the token cannot be routed through one.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        max_retries: int,
        backoff_base: float,
        api_key: str | None = None,
        max_total_seconds: float = 50.0,
        max_response_bytes: int = 64 * 1024 * 1024,
        retry_rate_limited: bool = False,
        allow_insecure_key: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if api_key and not allow_insecure_key and not url_may_carry_key(base_url):
            raise InferenceConfigError(
                "an inference API key is configured but the server URL is plain "
                "http to a host that may be public: use https, a cluster-internal "
                "name, or set ALLOW_INSECURE_KEY=true for that service to accept "
                "the risk explicitly."
            )
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=headers,
            transport=transport,
            trust_env=False,
        )
        self._timeout = timeout
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_total = max_total_seconds
        self._max_bytes = max_response_bytes
        self._retry_rate_limited = retry_rate_limited

    def _retryable(self, status: int) -> bool:
        if status == _RATE_LIMITED:
            return self._retry_rate_limited
        return status in _RETRYABLE_STATUS

    async def _post_capped(
        self, path: str, payload: dict[str, Any], timeout: float
    ) -> Any:
        """One POST; the body is read through the size cap, never past it.

        Returns the decoded JSON on success, the status code on an HTTP error.
        """
        request = self._client.build_request(
            "POST", path, json=payload, timeout=httpx.Timeout(timeout)
        )
        response = await self._client.send(request, stream=True)
        try:
            if response.status_code >= 400:
                return response.status_code
            declared = response.headers.get("content-length", "")
            if declared.isdigit() and int(declared) > self._max_bytes:
                raise InferenceError(
                    f"POST {path}: response larger than {self._max_bytes} bytes"
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > self._max_bytes:
                    raise InferenceError(
                        f"POST {path}: response larger than {self._max_bytes} bytes"
                    )
        finally:
            await response.aclose()
        return httpx.Response(200, content=bytes(body)).json()

    async def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        """POST ``payload`` and return the decoded JSON body."""
        last: Exception | None = None
        started = time.monotonic()
        attempts = 0
        for attempt in range(self._max_retries + 1):
            remaining = self._max_total - (time.monotonic() - started)
            if remaining <= 0:
                break
            attempts += 1
            try:
                outcome = await self._post_capped(
                    path, payload, min(self._timeout, remaining)
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = exc
            else:
                if not isinstance(outcome, int):
                    return outcome
                # The upstream body is never echoed: a TEI error text can name
                # model paths or internal hosts that a caller has no use for.
                if not self._retryable(outcome):
                    raise InferenceError(f"POST {path} failed: HTTP {outcome}")
                last = InferenceError(f"POST {path}: HTTP {outcome}")
            if attempt < self._max_retries:
                delay = self._backoff_base * (2**attempt)
                if time.monotonic() - started + delay >= self._max_total:
                    break
                logger.warning(
                    "inference_retry", path=path, attempt=attempt + 1, delay=delay
                )
                await asyncio.sleep(delay)
        if attempts <= self._max_retries:
            raise InferenceError(
                f"POST {path} failed after {attempts} attempt(s) within the "
                f"{self._max_total}s budget: {last}"
            ) from last
        raise InferenceError(
            f"POST {path} failed after {attempts} attempts: {last}"
        ) from last

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()
