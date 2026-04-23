"""Prometheus metrics exporter for the Baselithbot plugin.

Provides counters, gauges, and histograms covering channels, sessions,
cron jobs, computer-use actions, and inbound events. If
``prometheus_client`` is not installed all setters become no-ops and
``render_metrics`` returns a stub note.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (  # type: ignore[import-not-found]
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )

    _HAS_PROM = True
except ImportError:
    _HAS_PROM = False
    Counter = None  # type: ignore[assignment,misc]
    Gauge = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]
    CONTENT_TYPE_LATEST = "text/plain"

    def generate_latest(*args: Any, **kwargs: Any) -> bytes:  # type: ignore[no-redef,misc]
        del args, kwargs
        return b"# prometheus_client not installed\n"


_NAMESPACE = "baselithbot"


class _NoopMetric:
    def labels(self, **_: Any) -> _NoopMetric:
        return self

    def inc(self, *_: Any, **__: Any) -> None:
        return None

    def set(self, *_: Any, **__: Any) -> None:
        return None

    def observe(self, *_: Any, **__: Any) -> None:
        return None


def _counter(name: str, doc: str, labels: list[str]) -> Any:
    if not _HAS_PROM or Counter is None:
        return _NoopMetric()
    return Counter(f"{_NAMESPACE}_{name}", doc, labels)


def _gauge(name: str, doc: str, labels: list[str]) -> Any:
    if not _HAS_PROM or Gauge is None:
        return _NoopMetric()
    return Gauge(f"{_NAMESPACE}_{name}", doc, labels)


def _histogram(name: str, doc: str, labels: list[str]) -> Any:
    if not _HAS_PROM or Histogram is None:
        return _NoopMetric()
    return Histogram(f"{_NAMESPACE}_{name}", doc, labels)


CHANNEL_SEND_TOTAL = _counter(
    "channel_send_total", "Outbound messages per channel", ["channel", "status"]
)
INBOUND_EVENT_TOTAL = _counter("inbound_event_total", "Inbound events per channel", ["channel"])
SESSION_ACTIVE = _gauge("session_active", "Currently active sessions", [])
COMPUTER_USE_ACTION_TOTAL = _counter(
    "computer_use_action_total",
    "Computer Use action invocations",
    ["action", "outcome"],
)
COMPUTER_USE_LATENCY = _histogram(
    "computer_use_latency_seconds",
    "Latency of Computer Use actions",
    ["action"],
)
CRON_JOB_RUNS_TOTAL = _counter("cron_job_runs_total", "Cron job executions", ["job", "outcome"])


def render_metrics() -> tuple[bytes, str]:
    """Return ``(payload, content_type)`` for ``/metrics`` HTTP responses."""
    return generate_latest(), CONTENT_TYPE_LATEST


def is_prometheus_available() -> bool:
    return _HAS_PROM


__all__ = [
    "CHANNEL_SEND_TOTAL",
    "INBOUND_EVENT_TOTAL",
    "SESSION_ACTIVE",
    "COMPUTER_USE_ACTION_TOTAL",
    "COMPUTER_USE_LATENCY",
    "CRON_JOB_RUNS_TOTAL",
    "render_metrics",
    "is_prometheus_available",
]
