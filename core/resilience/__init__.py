"""
Core Resilience Module.

Provides resilience patterns for robust agent operations:
- Circuit Breaker: Prevent cascading failures
- Rate Limiting: API protection
- Retry with exponential backoff
- Timeout decorators
- Bulkhead (concurrency limiting)
- Graceful Shutdown
"""

from core.resilience.bulkhead import Bulkhead
from core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerError,
    CircuitState,
    get_circuit_breaker,
)
from core.resilience.distributed_lock import (
    DistributedLock,
    LockError,
    LockNotAcquired,
    get_distributed_lock,
)
from core.resilience.rate_limiter import (
    InMemoryRateLimiter,
    RateLimiter,
    RateLimitResult,
    RedisRateLimiter,
    get_api_limiter,
    get_llm_limiter,
)
from core.resilience.retry import TimeoutError, retry, timeout
from core.resilience.shutdown import GracefulShutdown, get_shutdown_handler

__all__ = [
    # Circuit Breaker
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerError",
    "CircuitState",
    "get_circuit_breaker",
    # Rate Limiter
    "RateLimiter",
    "RateLimitResult",
    "InMemoryRateLimiter",
    "RedisRateLimiter",
    "get_api_limiter",
    "get_llm_limiter",
    # Retry & Timeout
    "retry",
    "timeout",
    "TimeoutError",
    # Bulkhead
    "Bulkhead",
    # Distributed Lock
    "DistributedLock",
    "LockError",
    "LockNotAcquired",
    "get_distributed_lock",
    # Shutdown
    "GracefulShutdown",
    "get_shutdown_handler",
]
