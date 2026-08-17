"""
A2A Client

HTTP client for communicating with remote agents.
Includes retry logic, circuit breaker integration, and health checks.
"""

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from core.observability.logging import get_logger
from core.security.http import create_hardened_async_client
from core.security.ssrf import SsrfPolicy, assert_url_safe_async

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

from .agent_card import AgentCard
from .protocol import (
    A2AMessage,
    A2ARequest,
    A2AResponse,
    ErrorCode,
)
from .security import build_signature_headers, get_a2a_shared_secret

logger = get_logger(__name__)

_ENV_ALLOW_INTERNAL_ENDPOINTS = "A2A_ALLOW_INTERNAL_ENDPOINTS"

# Transient 4xx statuses worth retrying; every other 4xx is deterministic.
_RETRYABLE_4XX = frozenset({408, 429})


def _is_retryable_error(exc: Exception) -> bool:
    """Whether a failed invoke attempt can plausibly succeed on retry.

    Transport errors and timeouts are retryable; HTTP responses are retryable
    only for 5xx and the transient 4xx (408 Request Timeout, 429 Too Many
    Requests). Deterministic client errors (bad request, failed signature,
    unknown method) and local errors (serialization) fail identically every
    time — retrying them re-executes a non-idempotent invoke for nothing.
    """
    if httpx is None:  # pragma: no cover - httpx guaranteed by connect()
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status >= 500 or status in _RETRYABLE_4XX
    return isinstance(exc, (httpx.TransportError, asyncio.TimeoutError))


def _default_allow_internal_endpoints() -> bool:
    """Env-overridable default for ``A2AClientConfig.allow_internal_endpoints``.

    Deliberately secure-by-*inclusion* rather than secure-by-exclusion: A2A
    meshes commonly run peer agents on private networks, so allowing internal
    hosts is the **primary** use case here (see :attr:`A2AClient.endpoint`),
    not an opt-in escape hatch like ``BASELITH_BROWSER_ALLOW_INTERNAL`` or
    ``MCP_ALLOW_INTERNAL_ENDPOINTS`` elsewhere in the framework. Read from
    ``A2A_ALLOW_INTERNAL_ENDPOINTS`` (truthy: ``1``/``true``/``yes``/``on``,
    case-insensitive) for symmetry with those knobs; defaults to ``true``.
    Set to ``false`` for deployments that only ever talk to external peers
    and want the stricter posture.
    """
    raw = os.environ.get(_ENV_ALLOW_INTERNAL_ENDPOINTS, "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass
class A2AClientConfig:
    """Configuration for A2A client.

    Attributes:
        timeout: Per-request HTTP timeout in seconds.
        max_retries: Maximum invoke attempts before giving up.
        retry_delay: Base delay between retries in seconds.
        retry_backoff: Multiplier applied to ``retry_delay`` per attempt.
        health_check_interval: Seconds between passive health checks.
        circuit_breaker_threshold: Consecutive failures before the circuit
            opens.
        circuit_breaker_timeout: Seconds the circuit stays open before a
            half-open retry is allowed.
        allow_internal_endpoints: Whether the SSRF guard permits requests to
            private/loopback/link-local peer hosts. Defaults to the
            ``A2A_ALLOW_INTERNAL_ENDPOINTS`` env var (``true`` if unset)
            because A2A meshes commonly run peer agents on internal networks
            (see :attr:`A2AClient.endpoint`). Set to ``False`` (or the env
            var to ``false``) for deployments that only ever talk to
            external peers and want the stricter posture.
    """

    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    health_check_interval: float = 60.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0
    allow_internal_endpoints: bool = field(
        default_factory=_default_allow_internal_endpoints
    )


class CircuitState:
    """Circuit breaker state."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class A2AClient:
    """
    HTTP client for A2A protocol communication.

    Features:
    - Async HTTP requests with retry
    - Circuit breaker pattern
    - Health check support
    - Request/response serialization

    Example:
        ```python
        client = A2AClient(agent_card)
        await client.connect()

        response = await client.invoke("search", {"query": "test"})
        if response.success:
            print(response.result)

        await client.close()
        ```
    """

    def __init__(
        self,
        agent_card: AgentCard,
        config: A2AClientConfig | None = None,
    ):
        """
        Initialize A2A client.

        Args:
            agent_card: Target agent's card with endpoint
            config: Client configuration
        """
        self.agent_card = agent_card
        self.config = config or A2AClientConfig()

        # HTTP client
        self._client: httpx.AsyncClient | None = None

        # Circuit breaker state
        self._circuit_state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None

        # Health
        self._is_healthy = True
        self._last_health_check: float | None = None

    @property
    def endpoint(self) -> str:
        """Get the validated agent endpoint.

        Enforces an ``http(s)`` scheme so a malicious or misconfigured agent
        card cannot coerce the client into ``file://``/``gopher://`` style
        requests. Private/internal hosts are intentionally allowed: A2A meshes
        commonly run peer agents on internal networks.
        """
        endpoint = self.agent_card.endpoint
        if not endpoint:
            raise ValueError(f"Agent {self.agent_card.name} has no endpoint")
        scheme = urlparse(endpoint).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"Agent {self.agent_card.name} endpoint must use http(s); "
                f"got scheme '{scheme or 'none'}'"
            )
        return endpoint

    async def _assert_endpoint_safe(self, url: str) -> None:
        """Reject an unsafe target URL before any network I/O.

        Defense-in-depth ahead of the hardened transport (which re-validates
        and IP-pins every request/redirect at the wire). Async because DNS
        resolution is blocking: ``connect()`` awaits this on the event loop,
        so the check must not perform a synchronous ``socket.getaddrinfo``
        call directly on it (see :func:`core.security.ssrf.assert_url_safe_async`,
        which offloads to a worker thread). Internal hosts are allowed by
        default because A2A meshes commonly run peer agents on private
        networks — see :attr:`endpoint`; set
        ``A2AClientConfig(allow_internal_endpoints=False)`` for a stricter
        posture.

        Raises:
            SsrfError: If the URL is not a safe outbound target.
        """
        policy = SsrfPolicy(allow_internal=self.config.allow_internal_endpoints)
        await assert_url_safe_async(url, policy=policy)

    async def connect(self) -> None:
        """Initialize HTTP client."""
        if httpx is None:
            raise ImportError(
                "httpx is required for A2A client. Install with: pip install httpx"
            )

        await self._assert_endpoint_safe(self.endpoint)
        policy = SsrfPolicy(allow_internal=self.config.allow_internal_endpoints)
        self._client = create_hardened_async_client(
            policy=policy,
            timeout=self.config.timeout,
            headers={"Content-Type": "application/json"},
        )
        logger.info(f"A2A client connected to {self.agent_card.name}")

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info(f"A2A client disconnected from {self.agent_card.name}")

    async def invoke(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> A2AResponse:
        """
        Invoke a method on the remote agent.

        Args:
            method: Method name to invoke
            params: Method parameters
            timeout: Optional timeout override

        Returns:
            A2AResponse with result or error
        """
        if not self._client:
            await self.connect()

        # Check circuit breaker
        if not self._can_execute():
            return A2AResponse(
                success=False,
                error_code=ErrorCode.AGENT_UNAVAILABLE,
                error_message="Circuit breaker is open",
            )

        request = A2ARequest(
            method=method,
            params=params or {},
            timeout=timeout or self.config.timeout,
        )

        start_time = time.time()
        last_error: Exception | None = None

        for attempt in range(self.config.max_retries):
            try:
                response = await self._execute_request(request)
                self._record_success()
                return response
            except Exception as e:
                last_error = e
                logger.warning(f"A2A request failed (attempt {attempt + 1}): {e}")

                # Client errors (4xx) are deterministic — a bad request, a
                # failed signature, an unknown method will fail identically on
                # every retry, so re-sending only burns the retry budget and
                # re-executes a non-idempotent invoke on the peer. Only
                # transport errors, timeouts, and 5xx are worth retrying
                # (408/429 are the transient exceptions within 4xx).
                if not _is_retryable_error(e):
                    break

                if attempt < self.config.max_retries - 1:
                    # Full jitter on the exponential backoff: when many peers
                    # fail together (shared dependency down), synchronized
                    # retries stampede the recovering service.
                    delay = self.config.retry_delay * (
                        self.config.retry_backoff**attempt
                    )
                    await asyncio.sleep(delay * (0.5 + random.random() / 2))

        # All retries failed
        self._record_failure()
        latency = (time.time() - start_time) * 1000

        return A2AResponse(
            success=False,
            error_code=ErrorCode.INTERNAL_ERROR,
            error_message=str(last_error),
            latency_ms=latency,
        )

    async def _execute_request(self, request: A2ARequest) -> A2AResponse:
        """Execute single HTTP request."""
        if self._client is None:
            raise RuntimeError("Client not connected. Call connect() first.")

        message = request.to_message(to_agent=self.agent_card.name)
        url = f"{self.endpoint}/a2a/invoke"

        # Serialize once so the signature is computed over the exact bytes
        # sent on the wire. Signing is active only when the shared secret
        # (BASELITH_A2A_SHARED_SECRET) is configured.
        body = json.dumps(message.to_dict()).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        secret = get_a2a_shared_secret()
        if secret is not None:
            headers.update(build_signature_headers(body, secret))

        start = time.time()
        response = await self._client.post(url, content=body, headers=headers)
        latency = (time.time() - start) * 1000

        response.raise_for_status()

        response_data = response.json()
        response_msg = A2AMessage.from_dict(response_data)

        return A2AResponse.from_message(response_msg, latency_ms=latency)

    async def health_check(self) -> bool:
        """
        Check if remote agent is healthy.

        Returns:
            True if agent responds to health check
        """
        if not self._client:
            await self.connect()

        try:
            if self._client is None:
                raise RuntimeError("Client not connected")
            url = f"{self.endpoint}/a2a/health"
            response = await self._client.get(url, timeout=5.0)
            self._is_healthy = response.status_code == 200
            self._last_health_check = time.time()
            return self._is_healthy
        except Exception as e:
            logger.warning(f"Health check failed for {self.agent_card.name}: {e}")
            self._is_healthy = False
            return False

    def _can_execute(self) -> bool:
        """Check if request can be executed (circuit breaker logic)."""
        if self._circuit_state == CircuitState.CLOSED:
            return True

        if self._circuit_state == CircuitState.OPEN:
            # Check if timeout has passed
            if self._last_failure_time:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.config.circuit_breaker_timeout:
                    self._circuit_state = CircuitState.HALF_OPEN
                    return True
            return False

        # Half-open: allow one request
        return True

    def _record_success(self) -> None:
        """Record successful request."""
        self._failure_count = 0
        if self._circuit_state == CircuitState.HALF_OPEN:
            self._circuit_state = CircuitState.CLOSED
            logger.info(f"Circuit breaker closed for {self.agent_card.name}")

    def _record_failure(self) -> None:
        """Record failed request."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.config.circuit_breaker_threshold:
            self._circuit_state = CircuitState.OPEN
            logger.warning(f"Circuit breaker opened for {self.agent_card.name}")

    @property
    def is_healthy(self) -> bool:
        """Check cached health status."""
        return self._is_healthy

    @property
    def circuit_state(self) -> str:
        """Get current circuit breaker state."""
        return self._circuit_state


class A2AClientPool:
    """
    Pool of A2A clients for multiple agents.

    Manages connections to multiple remote agents.
    """

    def __init__(self, config: A2AClientConfig | None = None):
        """Initialize client pool."""
        self.config = config or A2AClientConfig()
        self._clients: dict[str, A2AClient] = {}

    async def get_client(self, agent_card: AgentCard) -> A2AClient:
        """Get or create client for agent."""
        if agent_card.name not in self._clients:
            client = A2AClient(agent_card, self.config)
            await client.connect()
            self._clients[agent_card.name] = client
        return self._clients[agent_card.name]

    async def close_all(self) -> None:
        """Close all clients."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    async def health_check_all(self) -> dict[str, bool]:
        """Run health checks on all clients concurrently."""
        if not self._clients:
            return {}
        names = list(self._clients.keys())
        checks = await asyncio.gather(
            *(self._clients[name].health_check() for name in names)
        )
        return dict(zip(names, checks))
