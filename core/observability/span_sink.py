"""Process-local observation seam for completed spans.

Tracing normally leaves the process: the OTel SDK batches spans to an OTLP
collector (see :mod:`core.observability.otel`). That is the right destination
for production analysis, but it makes the *live* trace invisible to anything
running inside this process — a control-plane dashboard, a debug endpoint, a
test harness — unless an external backend is deployed.

This module adds the missing in-process fan-out, mirroring the LLM
``register_token_sink`` pattern in :mod:`core.services.llm._telemetry`:

* consumers register a callable and receive every completed span as a plain,
  provider-neutral :class:`SpanRecord`;
* sinks are resolved **at emit time**, so a consumer registered long after
  import (a plugin loaded at boot) still sees every subsequent span;
* emission is best-effort — a raising or slow sink never breaks the traced
  operation, and with no sink registered the cost is one boolean check.

Both span producers feed this seam: the homegrown
:class:`~core.observability.tracing.Tracer` (when the OTel SDK is not
installed) and the real SDK via the bridge processor in
:mod:`core.observability.span_bridge` (when it is). Exactly one of the two
paths is active, so a span is never delivered twice.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SpanRecord",
    "SpanSink",
    "emit_span",
    "has_span_sinks",
    "register_span_sink",
    "unregister_span_sink",
]


@dataclass(frozen=True)
class SpanRecord:
    """One completed span, normalized across the homegrown and SDK producers.

    Times are epoch seconds (float) so consumers can render a waterfall without
    knowing which producer emitted the span. ``attributes`` and ``events`` carry
    whatever the instrumentation set — consumers are responsible for redacting
    or truncating them before storage or display.
    """

    trace_id: str
    span_id: str
    name: str
    start_time: float
    end_time: float
    status: str = "unset"  # "ok" | "error" | "unset"
    parent_span_id: str | None = None
    service: str | None = None
    kind: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        """Wall-clock duration in milliseconds (never negative)."""
        return max(0.0, (self.end_time - self.start_time) * 1000.0)

    def to_dict(self) -> dict[str, Any]:
        """Plain-dict projection (JSON-serializable field set)."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "service": self.service,
            "kind": self.kind,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": dict(self.attributes),
            "events": list(self.events),
        }


SpanSink = Callable[[SpanRecord], None]

# Resolved at emit time (never bound into call sites), so late registration
# works. A plain list keeps the hot path allocation-free when it is empty.
_span_sinks: list[SpanSink] = []


def register_span_sink(sink: SpanSink) -> None:
    """Subscribe *sink* to every completed span. Idempotent.

    Sinks are passive observers (dashboards, debug buffers, tests): exceptions
    they raise are swallowed so instrumentation can never break the operation
    being traced.
    """
    if sink not in _span_sinks:
        _span_sinks.append(sink)


def unregister_span_sink(sink: SpanSink) -> None:
    """Remove a previously registered sink (no-op when absent)."""
    if sink in _span_sinks:
        _span_sinks.remove(sink)


def has_span_sinks() -> bool:
    """True when at least one sink is registered.

    Producers call this before building a :class:`SpanRecord` so an
    unobserved process pays nothing beyond the check.
    """
    return bool(_span_sinks)


def emit_span(record: SpanRecord) -> None:
    """Deliver *record* to every registered sink (best-effort, never raises)."""
    for sink in list(_span_sinks):
        try:
            sink(record)
        except Exception:
            pass
