"""
A2A Client

HTTP client for communicating with remote agents.
Includes retry logic, circuit breaker integration, and health checks.
"""

import asyncio
from core.observability.logging import get_logger
import time
from typing import Any, Dict, Optional
from dataclasses import dataclass

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

logger = get_logger(__name__)


@dataclass
class A2AClientConfig:
    """Configuration for A2A client."""

    timeout: float = 30.0
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    health_check_interval: float = 60.0
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: float = 60.0


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
        config: Optional[A2AClientConfig] = None,
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
        self._client: Optional["httpx.AsyncClient"] = None

        # Circuit breaker state
        self._circuit_state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None

        # Health
        self._is_healthy = True
        self._last_health_check: Optional[float] = None

    @property
    def endpoint(self) -> str:
        """Get agent endpoint."""
        if not self.agent_card.endpoint:
            raise ValueError(f"Agent {self.agent_card.name} has no endpoint")
        return self.agent_card.endpoint

    async def connect(self) -> None:
        """Initialize HTTP client."""
        if httpx is None:
            raise ImportError(
                "httpx is required for A2A client. Install with: pip install httpx"
            )

        self._client = httpx.AsyncClient(
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
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
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
        last_error: Optional[Exception] = None

        for attempt in range(self.config.max_retries):
            try:
                response = await self._execute_request(request)
                self._record_success()
                return response
            except Exception as e:
                last_error = e
                logger.warning(f"A2A request failed (attempt {attempt + 1}): {e}")

                if attempt < self.config.max_retries - 1:
                    delay = self.config.retry_delay * (
                        self.config.retry_backoff**attempt
                    )
                    await asyncio.sleep(delay)

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

        start = time.time()
        response = await self._client.post(url, json=message.to_dict())
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

    def __init__(self, config: Optional[A2AClientConfig] = None):
        """Initialize client pool."""
        self.config = config or A2AClientConfig()
        self._clients: Dict[str, A2AClient] = {}

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

    async def health_check_all(self) -> Dict[str, bool]:
        """Run health checks on all clients."""
        results = {}
        for name, client in self._clients.items():
            results[name] = await client.health_check()
        return results
