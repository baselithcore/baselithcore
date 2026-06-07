"""Shared Prometheus metrics for the security middleware.

Defined in a dedicated module so the security manager and the ASGI security
middlewares can share a single ``Counter`` instance without importing each
other (a Prometheus collector may only be registered once per process).
"""

from __future__ import annotations

from prometheus_client import Counter

# Global security event counter (auth failures, rate limiting, oversized
# requests). Registered exactly once at import time.
SECURITY_EVENTS = Counter(
    "security_events_total",
    "Security events (auth/rate-limit)",
    ["reason"],
)

__all__ = ["SECURITY_EVENTS"]
