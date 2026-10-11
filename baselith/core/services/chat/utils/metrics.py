"""Prometheus metrics for chat operations (split from the chat service)."""

from __future__ import annotations

__all__ = ["record_chat_metric"]


def record_chat_metric(name: str, route: str = "", value: float | None = None) -> None:
    """Update one chat metric.

    Args:
        name: Metric identifier (e.g., ``chat_requests_total``).
        route: Call context (``sync``, ``async``, ``stream``).
        value: Optional numeric value (e.g., latency duration).
    """
    try:
        from baselith.core.observability.metrics import (
            CHAT_REQUEST_ERRORS_TOTAL,
            CHAT_REQUEST_LATENCY_SECONDS,
            CHAT_REQUESTS_TOTAL,
        )

        if name == "chat_requests_total":
            CHAT_REQUESTS_TOTAL.labels(route=route).inc()
        elif name == "chat_request_errors_total":
            CHAT_REQUEST_ERRORS_TOTAL.labels(route=route, reason="exception").inc()
        elif name == "chat_request_latency" and value is not None:
            CHAT_REQUEST_LATENCY_SECONDS.labels(route=route).observe(value)
    except ImportError:
        pass  # Fallback: ignore metrics if the module is unavailable or disabled.
