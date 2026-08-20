"""Span exporters for the homegrown tracer.

Split out of :mod:`core.observability.tracing` so each file stays focused (and
under the repository's file-size cap): this module owns *where completed spans
go* (console, memory, the OTel bridge), while ``tracing`` owns *how spans are
created*. ``tracing`` re-exports every name here, so existing imports such as
``from core.observability.tracing import ConsoleExporter`` keep working.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.config import get_app_config
from core.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from core.observability.tracing import Span

logger = get_logger(__name__)


def _otel_active() -> bool:
    """True when the real OTel SDK provider is installed (see otel.py)."""
    try:
        from core.observability.otel import is_initialized

        return is_initialized()
    except Exception:  # pragma: no cover - defensive
        return False


class SpanExporter:
    """Base class for span exporters."""

    def export(self, spans: list[Span]) -> None:
        """Export completed spans."""
        pass


class ConsoleExporter(SpanExporter):
    """Exports spans to console/logger."""

    def __init__(self, log_level: int = logging.DEBUG) -> None:
        self._log_level = log_level

    def export(self, spans: list[Span]) -> None:
        for span in spans:
            logger.log(
                self._log_level,
                f"[TRACE] {span.name} "
                f"trace_id={span.context.trace_id[:8]} "
                f"duration={span.duration_ms:.2f}ms "
                f"status={span.status.value}",
            )


class InMemoryExporter(SpanExporter):
    """Stores spans in memory for testing."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, spans: list[Span]) -> None:
        self.spans.extend(spans)

    def clear(self) -> None:
        self.spans.clear()


class OTLPExporter(SpanExporter):
    """
    Diagnostic exporter for homegrown spans when OTel is active.

    Provider installation is owned by :mod:`core.observability.otel`. Real
    export to the collector happens via the live OTel span the ``Tracer``
    bridge opens per span (see ``Tracer.start_span``); this exporter only logs
    a debug line for the homegrown mirror, and falls back to the console
    exporter when the OTel SDK is not installed.
    """

    def __init__(self, endpoint: str | None = None) -> None:
        if endpoint is None:
            config = get_app_config()
            endpoint = config.telemetry_otel_endpoint or "http://localhost:4317"

        self._endpoint = endpoint
        self._fallback = ConsoleExporter()

    @property
    def _initialized(self) -> bool:
        return _otel_active()

    def export(self, spans: list[Span]) -> None:
        if not _otel_active():
            self._fallback.export(spans)
            return

        # Real spans already reach the collector through the Tracer→OTel bridge.
        for span in spans:
            logger.debug(
                f"[OTEL] Span mirrored: {span.name} "
                f"trace_id={span.context.trace_id[:8]} "
                f"duration={span.duration_ms:.2f}ms"
            )


__all__ = [
    "ConsoleExporter",
    "InMemoryExporter",
    "OTLPExporter",
    "SpanExporter",
]
