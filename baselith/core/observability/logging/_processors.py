"""structlog processors split out of :mod:`baselith.core.observability.logging`.

Both are pure event-dict transforms with no dependency on the rest of the
pipeline's module state, so they live here to keep that module under the
file-size cap.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


def add_otel_context(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """
    structlog processor that injects the active OpenTelemetry trace context.

    Adds ``trace_id``/``span_id`` (zero-padded hex, W3C format) to every log
    entry when a span is recording, enabling trace↔log correlation in
    backends like Grafana/Tempo/Loki. No-op when the OTel SDK is absent or no
    span is active, so it is always safe to keep in the processor chain.
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = f"{ctx.trace_id:032x}"
            event_dict["span_id"] = f"{ctx.span_id:016x}"
    except Exception:  # silent-ok: structlog processor; logging here recurses
        pass
    return event_dict


def _count_log_record(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: tally emitted records by level, for alerting.

    The log viewers in this codebase (baselithcontrol's ring/Redis tail) cap
    retention, so an error spike that scrolls past the window leaves no
    trace there. A Prometheus counter is cheap, unbounded in time, and is
    what an alerting rule can actually fire on. Imported lazily (like
    :func:`add_otel_context` imports OTel) so a module that only needs
    logging never pays for the metrics stack, and a failure here can never
    break the pipeline it instruments.
    """
    try:
        from baselith.core.observability.metrics import LOG_RECORDS_TOTAL

        LOG_RECORDS_TOTAL.labels(
            level=str(event_dict.get("level", "info")).upper()
        ).inc()
    except Exception:  # silent-ok: a metrics failure must never break logging
        pass
    return event_dict


__all__ = ["add_otel_context", "_count_log_record"]
