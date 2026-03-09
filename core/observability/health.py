"""
Health check utilities with caching.

Provides cached health check for expensive service verifications.
"""

from core.observability.logging import get_logger
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Optional

logger = get_logger(__name__)


@dataclass
class HealthStatus:
    """Health check result."""

    status: str  # "healthy", "degraded", "unhealthy"
    services: Dict[str, bool]
    latency_ms: float
    cached: bool = False


class CachedHealthCheck:
    """
    Health check with caching to reduce overhead.

    Usage:
        health_checker = CachedHealthCheck(cache_ttl=30)

        async def check_services():
            return {
                "database": await check_db(),
                "redis": await check_redis(),
            }

        status = await health_checker.get_status(check_services)
    """

    def __init__(self, cache_ttl: int = 30):
        """
        Initialize health checker.

        Args:
            cache_ttl: Cache TTL in seconds (default 30s)
        """
        self._cache_ttl = cache_ttl
        self._cached_status: Optional[HealthStatus] = None
        self._cache_time: float = 0.0

    async def get_status(
        self, check_fn: Callable[[], Awaitable[Dict[str, bool]]]
    ) -> HealthStatus:
        """
        Get health status, using cache if available.

        Args:
            check_fn: Async function that returns Dict[str, bool] of service statuses

        Returns:
            HealthStatus with cached flag
        """
        now = time.time()

        # Return cached if still valid
        if self._cached_status and (now - self._cache_time) < self._cache_ttl:
            return HealthStatus(
                status=self._cached_status.status,
                services=self._cached_status.services,
                latency_ms=0.0,
                cached=True,
            )

        # Perform actual check
        start = time.perf_counter()
        try:
            services = await check_fn()
            elapsed_ms = (time.perf_counter() - start) * 1000

            # Determine overall status
            if all(services.values()):
                status = "healthy"
            elif any(services.values()):
                status = "degraded"
            else:
                status = "unhealthy"

            result = HealthStatus(
                status=status,
                services=services,
                latency_ms=elapsed_ms,
                cached=False,
            )

            # Update cache
            self._cached_status = result
            self._cache_time = now

            return result

        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Health check failed: {e}")
            return HealthStatus(
                status="unhealthy",
                services={"error": False},
                latency_ms=elapsed_ms,
                cached=False,
            )

    def invalidate(self) -> None:
        """
        Invalidate the currently cached health status.

        Forces the next call to get_status to perform a fresh check.
        """
        self._cached_status = None
        self._cache_time = 0.0


# Global instance
_health_checker: Optional[CachedHealthCheck] = None


def get_health_checker(cache_ttl: int = 30) -> CachedHealthCheck:
    """
    Retrieve or create the global singleton health checker instance.

    Args:
        cache_ttl: Default cache TTL if creating a new instance.

    Returns:
        The global CachedHealthCheck instance.
    """
    global _health_checker
    if _health_checker is None:
        _health_checker = CachedHealthCheck(cache_ttl=cache_ttl)
    return _health_checker
