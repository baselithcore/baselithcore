"""OTel SDK → in-process span-sink bridge.

When the real OpenTelemetry SDK is installed, every span (auto-instrumented
FastAPI/HTTPX/Redis/psycopg spans included) flows through the ``TracerProvider``
and out to the OTLP collector. This module attaches one extra ``SpanProcessor``
that also hands each finished span to the local
:mod:`core.observability.span_sink` consumers, so in-process readers see the
*same* spans an external backend would — no second instrumentation pass, no
duplicate spans, no dependency on a collector being deployed.

Design rules:

* **Zero-cost when unobserved.** ``on_end`` returns immediately unless a sink
  is registered, so a deployment with no in-process consumer pays one boolean
  check per span.
* **Never blocks export.** Conversion runs inline (sinks must be cheap, they
  are dashboards/ring buffers) and every failure is swallowed — the OTLP
  pipeline is unaffected.
* **Optional dependency.** The SDK is imported lazily inside the installer, so
  this module imports fine in a checkout without OpenTelemetry.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.observability.span_sink import SpanRecord, emit_span, has_span_sinks

logger = get_logger(__name__)

__all__ = ["install_span_sink_bridge", "readable_span_to_record"]

# OTel timestamps are integer nanoseconds since the epoch.
_NS = 1_000_000_000.0


def _hex(value: int | None, width: int) -> str | None:
    """Format an OTel trace/span id as lowercase hex, like the wire format."""
    if not value:
        return None
    return format(value, f"0{width}x")


def readable_span_to_record(span: Any) -> SpanRecord | None:
    """Convert an SDK ``ReadableSpan`` to a provider-neutral :class:`SpanRecord`.

    Returns ``None`` for a span that cannot be represented (no context, or an
    unfinished span), so callers can skip it without special-casing.
    """
    context = getattr(span, "context", None)
    if context is None or not getattr(span, "end_time", None):
        return None

    parent = getattr(span, "parent", None)
    status = getattr(span, "status", None)
    status_code = getattr(getattr(status, "status_code", None), "name", "") or ""
    normalized = {"OK": "ok", "ERROR": "error"}.get(status_code.upper(), "unset")

    resource_attrs = getattr(getattr(span, "resource", None), "attributes", {}) or {}
    kind = getattr(getattr(span, "kind", None), "name", None)

    events = []
    for event in getattr(span, "events", ()) or ():
        events.append(
            {
                "name": getattr(event, "name", ""),
                "timestamp": getattr(event, "timestamp", 0) / _NS,
                "attributes": dict(getattr(event, "attributes", {}) or {}),
            }
        )

    return SpanRecord(
        trace_id=_hex(getattr(context, "trace_id", None), 32) or "",
        span_id=_hex(getattr(context, "span_id", None), 16) or "",
        parent_span_id=_hex(getattr(parent, "span_id", None), 16),
        name=str(getattr(span, "name", "")),
        service=str(resource_attrs.get("service.name") or "") or None,
        kind=kind.lower() if isinstance(kind, str) else None,
        start_time=getattr(span, "start_time", 0) / _NS,
        end_time=getattr(span, "end_time", 0) / _NS,
        status=normalized,
        attributes=dict(getattr(span, "attributes", {}) or {}),
        events=events,
    )


def install_span_sink_bridge(provider: Any) -> bool:
    """Attach the sink-forwarding processor to *provider*.

    Best-effort: returns ``False`` (and logs at debug level) when the SDK is
    unavailable or the processor cannot be attached — telemetry setup must
    never fail because the in-process mirror could not be installed.
    """
    try:
        from opentelemetry.sdk.trace import SpanProcessor

        class _SinkSpanProcessor(SpanProcessor):  # type: ignore[misc]
            """Forwards every finished span to the local span sinks."""

            def on_start(self, span: Any, parent_context: Any = None) -> None:
                return None

            def on_end(self, span: Any) -> None:
                if not has_span_sinks():
                    return
                try:
                    record = readable_span_to_record(span)
                except Exception:
                    return
                if record is not None:
                    emit_span(record)

            def shutdown(self) -> None:
                return None

            def force_flush(self, timeout_millis: int = 30_000) -> bool:
                return True

        provider.add_span_processor(_SinkSpanProcessor())
        return True
    except Exception as exc:
        logger.debug("[OTEL] span-sink bridge not installed: %s", exc)
        return False
