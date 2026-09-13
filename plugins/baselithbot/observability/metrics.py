"""Prometheus metrics exporter for the Baselithbot plugin.

Provides counters, gauges, and histograms covering channels, sessions,
cron jobs, computer-use actions, and inbound events. If
``prometheus_client`` is not installed all setters become no-ops and
``render_metrics`` returns a stub note.

Exposition format is negotiated from the caller's ``Accept`` header. These
metrics live on the process-wide default registry, the same one
``core.observability.metric_context`` attaches ``trace_id`` exemplars to — and
the Prometheus text format has no syntax for exemplars, so returning it
unconditionally recorded that link and discarded it on the way out. This
mirrors the core ``/metrics`` router exactly; the two exposition paths must not
disagree about what a scraper asked for.
"""

from __future__ import annotations

from typing import Any

try:
    from prometheus_client import (  # type: ignore[import-not-found]
        CONTENT_TYPE_LATEST,
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    from prometheus_client.exposition import (  # type: ignore[import-not-found]
        choose_encoder,
    )

    _HAS_PROM = True
except ImportError:
    _HAS_PROM = False
    Counter = None  # type: ignore[assignment,misc]
    Gauge = None  # type: ignore[assignment,misc]
    Histogram = None  # type: ignore[assignment,misc]
    REGISTRY = None  # type: ignore[assignment]
    CONTENT_TYPE_LATEST = "text/plain"

    def generate_latest(*args: Any, **kwargs: Any) -> bytes:  # type: ignore[no-redef,misc]
        del args, kwargs
        return b"# prometheus_client not installed\n"

    def choose_encoder(accept_header: str) -> Any:  # type: ignore[misc]
        del accept_header
        return generate_latest, CONTENT_TYPE_LATEST


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


def render_metrics(accept_header: str = "", *, registry: Any | None = None) -> tuple[bytes, str]:
    """Serialize the metrics registry in the format *accept_header* asks for.

    Negotiation is delegated to prometheus_client's own ``choose_encoder``
    rather than hand-parsed: it is the function the library keeps in step with
    the spec (format version, plus the escaping parameter that arrived with
    UTF-8 name support), and it already falls back to the Prometheus text
    format for any header it does not recognise — an absent or empty one
    included.

    Args:
        accept_header: Raw ``Accept`` header value. Defaults to ``""``, which
            yields the plain text format; callers that embed the payload in
            something other than an HTTP metrics response (the diagnostics
            passthrough decodes it into a JSON string field) must leave it
            unset, or they would get the OpenMetrics encoding.
        registry: Registry to serialize. Defaults to the process-wide
            ``REGISTRY`` these metrics are registered on; injectable for tests.

    Returns:
        ``(payload, content_type)`` for a ``/metrics`` HTTP response. Only the
        OpenMetrics encoding carries exemplars; the text format has no syntax
        for them, so a scraper that does not ask for OpenMetrics still gets a
        parseable body, simply without the trace links.
    """
    encoder, content_type = choose_encoder(accept_header)
    # One call shape for both branches. Without prometheus_client, REGISTRY is
    # None and the stub encoder discards its arguments to return the "not
    # installed" note; branching to a no-argument call instead made this a
    # type error against the real `generate_latest`, which takes a registry.
    return encoder(REGISTRY if registry is None else registry), content_type


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
