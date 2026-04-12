"""
Service-Level Fault Tolerance and Stability.

Implements the Circuit Breaker pattern to ensure system stability
during external service degradations. Prevents cascading failures by
isolating faulty dependencies and allowing them to recover through
monitored state transitions (Closed -> Open -> Half-Open).
"""

import asyncio
import inspect
import threading
from core.observability.logging import get_logger
import time
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from types import TracebackType
from typing import Any, Awaitable, Callable, Literal, Optional, TypeVar

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitStats:
    """Circuit breaker statistics."""

    failures: int = 0
    successes: int = 0
    last_failure_time: float = 0
    last_success_time: float = 0


class CircuitBreaker:
    """
    Guardian for external I/O and service dependencies.

    Monitors the success/failure rates of wrapped calls. If failures
    exceed a configurable threshold, the circuit 'trips', immediately
    rejecting subsequent calls to protect resources. Automatically
    handles state transitions based on timeouts and trial calls.
    """

    def __init__(
        self,
        name: str,
        fail_max: Optional[int] = None,
        reset_timeout: Optional[int] = None,
        half_open_max: Optional[int] = None,
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Name for logging
            fail_max: Failures before opening circuit
            reset_timeout: Seconds before trying half-open
            half_open_max: Max requests in half-open state
        """
        from core.config.resilience import get_resilience_config

        config = get_resilience_config()

        self.name = name
        self.fail_max = fail_max if fail_max is not None else config.cb_fail_max
        self.reset_timeout = (
            reset_timeout if reset_timeout is not None else config.cb_reset_timeout
        )
        self.half_open_max = (
            half_open_max if half_open_max is not None else config.cb_half_open_max
        )

        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._half_open_attempts = 0
        self._sync_lock = threading.Lock()
        self._async_lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        """Get current state, checking for timeout."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._stats.last_failure_time
            if elapsed >= self.reset_timeout:
                logger.info(f"Circuit {self.name}: OPEN -> HALF_OPEN")
                self._state = CircuitState.HALF_OPEN
                self._half_open_attempts = 0
        return self._state

    def _record_success(self) -> None:
        """Record successful call."""
        with self._sync_lock:
            self._stats.successes += 1
            self._stats.last_success_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                logger.info(f"Circuit {self.name}: HALF_OPEN -> CLOSED")
                self._state = CircuitState.CLOSED
                self._stats.failures = 0

    def _record_failure(self, _exc: BaseException) -> None:
        """Record failed call."""
        with self._sync_lock:
            self._stats.failures += 1
            self._stats.last_failure_time = time.time()

            if self._state == CircuitState.CLOSED:
                if self._stats.failures >= self.fail_max:
                    logger.warning(f"Circuit {self.name}: CLOSED -> OPEN")
                    self._state = CircuitState.OPEN
            elif self._state == CircuitState.HALF_OPEN:
                logger.warning(f"Circuit {self.name}: HALF_OPEN -> OPEN")
                self._state = CircuitState.OPEN

    def __call__(self, func: Callable[..., T]) -> Callable[..., Any]:
        """Decorator usage — supports both sync and async functions."""
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                """Asynchronous execution wrapper."""
                return await self.async_call(func, *args, **kwargs)

            return async_wrapper

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            """Synchronous execution wrapper."""
            return self.call(func, *args, **kwargs)

        return wrapper

    def _check_state(self) -> None:
        """Check circuit state and raise if not callable. Must be called under lock."""
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitBreakerError(f"Circuit {self.name} is OPEN")
        if state == CircuitState.HALF_OPEN:
            if self._half_open_attempts >= self.half_open_max:
                raise CircuitBreakerError(f"Circuit {self.name} half-open limit")
            self._half_open_attempts += 1

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute function with circuit breaker protection."""
        with self._sync_lock:
            self._check_state()
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure(e)
            raise

    async def async_call(
        self,
        func: Callable[..., Awaitable[T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute async function with circuit breaker protection."""
        async with self._async_lock:
            self._check_state()
        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure(e)
            raise

    def __enter__(self) -> "CircuitBreaker":
        """Context manager entry."""
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerError(f"Circuit {self.name} is OPEN")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        """Context manager exit."""
        if exc_type is not None:
            self._record_failure(
                exc_val if exc_val is not None else Exception("Unknown circuit error")
            )
        else:
            self._record_success()
        return False  # Don't suppress exceptions

    async def __aenter__(self) -> "CircuitBreaker":
        """Async context manager entry."""
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerError(f"Circuit {self.name} is OPEN")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        """Async context manager exit."""
        if exc_type is not None:
            self._record_failure(
                exc_val if exc_val is not None else Exception("Unknown circuit error")
            )
        else:
            self._record_success()
        return False

    def get_stats(self) -> dict[str, str | int]:
        """Get circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self._stats.failures,
            "successes": self._stats.successes,
            "fail_max": self.fail_max,
            "reset_timeout": self.reset_timeout,
        }


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""

    pass


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    fail_max: int = 5
    reset_timeout: int = 60
    half_open_max: int = 1


# Pre-configured circuit breakers
_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    name: str,
    config: Optional[CircuitBreakerConfig] = None,
) -> CircuitBreaker:
    """Get or create a named circuit breaker."""
    if name not in _circuit_breakers:
        if config:
            _circuit_breakers[name] = CircuitBreaker(
                name,
                fail_max=config.fail_max,
                reset_timeout=config.reset_timeout,
                half_open_max=config.half_open_max,
            )
        else:
            _circuit_breakers[name] = CircuitBreaker(name)
    return _circuit_breakers[name]
