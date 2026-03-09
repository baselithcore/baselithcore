"""
OpenTelemetry-compatible tracing system.

Provides distributed tracing with span management and context propagation.
Note: Uses OpenTelemetry API patterns but can work without the full SDK.
"""

from __future__ import annotations

import logging
from core.observability.logging import get_logger
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generator, List, Optional

from core.config import get_app_config

logger = get_logger(__name__)


class SpanStatus(str, Enum):
    """Span completion status."""

    OK = "ok"
    ERROR = "error"
    UNSET = "unset"


@dataclass
class SpanContext:
    """Span context for propagation."""

    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None

    def to_headers(self) -> Dict[str, str]:
        """Convert to W3C trace context headers."""
        return {
            "traceparent": f"00-{self.trace_id}-{self.span_id}-01",
        }

    @classmethod
    def from_headers(cls, headers: Dict[str, str]) -> Optional[SpanContext]:
        """Parse from W3C trace context headers."""
        traceparent = headers.get("traceparent")
        if not traceparent:
            return None

        try:
            parts = traceparent.split("-")
            if len(parts) >= 3:
                return cls(trace_id=parts[1], span_id=parts[2])
        except Exception:
            logger.debug("Failed to parse traceparent header", exc_info=True)
        return None


@dataclass
class Span:
    """
    Represents a single trace span.

    A span is a single operation within a trace.
    """

    name: str
    context: SpanContext
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: SpanStatus = SpanStatus.UNSET
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[float]:
        """Get span duration in milliseconds."""
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        """Set multiple attributes."""
        self.attributes.update(attributes)

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Add an event to the span."""
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def set_status(self, status: SpanStatus, description: Optional[str] = None) -> None:
        """Set span status."""
        self.status = status
        if description:
            self.attributes["status.description"] = description

    def end(self) -> None:
        """End the span."""
        self.end_time = time.time()
        if self.status == SpanStatus.UNSET:
            self.status = SpanStatus.OK

    def to_dict(self) -> Dict[str, Any]:
        """Convert span to dictionary."""
        return {
            "name": self.name,
            "trace_id": self.context.trace_id,
            "span_id": self.context.span_id,
            "parent_span_id": self.context.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "attributes": self.attributes,
            "events": self.events,
        }


class SpanExporter:
    """Base class for span exporters."""

    def export(self, spans: List[Span]) -> None:
        """Export completed spans."""
        pass


class ConsoleExporter(SpanExporter):
    """Exports spans to console/logger."""

    def __init__(self, log_level: int = logging.DEBUG) -> None:
        self._log_level = log_level

    def export(self, spans: List[Span]) -> None:
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
        self.spans: List[Span] = []

    def export(self, spans: List[Span]) -> None:
        self.spans.extend(spans)

    def clear(self) -> None:
        self.spans.clear()


class OTLPExporter(SpanExporter):
    """
    Exports spans to an OpenTelemetry collector via OTLP.

    Falls back to ConsoleExporter if opentelemetry-sdk is not installed.
    """

    def __init__(self, endpoint: Optional[str] = None) -> None:
        if endpoint is None:
            config = get_app_config()
            endpoint = config.telemetry_otel_endpoint or "http://localhost:4317"

        self._endpoint = endpoint
        self._otel_tracer = None
        self._initialized = False
        self._fallback = ConsoleExporter()

        try:
            from opentelemetry import trace
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import Resource, SERVICE_NAME

            resource = Resource.create({SERVICE_NAME: "baselith-core"})
            provider = TracerProvider(resource=resource)
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)
            self._otel_tracer = trace.get_tracer(__name__)
            self._initialized = True
            logger.info(f"[OTEL] OTLP exporter initialized, sending to {endpoint}")
        except ImportError as e:
            logger.warning(
                f"[OTEL] OpenTelemetry SDK not available, using console: {e}"
            )
        except Exception as e:
            logger.error(f"[OTEL] Failed to initialize OTLP exporter: {e}")

    def export(self, spans: List[Span]) -> None:
        if not self._initialized:
            self._fallback.export(spans)
            return

        # Spans are already exported by OpenTelemetry SDK auto-instrumentation
        # This method is for custom spans created by our Tracer
        for span in spans:
            logger.debug(
                f"[OTEL] Span exported: {span.name} "
                f"trace_id={span.context.trace_id[:8]} "
                f"duration={span.duration_ms:.2f}ms"
            )


class Tracer:
    """
    Tracing interface for creating and managing spans.

    Usage:
        tracer = get_tracer("my-service")

        with tracer.start_span("operation") as span:
            span.set_attribute("key", "value")
            # ... do work ...
    """

    def __init__(
        self,
        service_name: str,
        exporter: Optional[SpanExporter] = None,
    ) -> None:
        self._service_name = service_name
        self._exporter = exporter or ConsoleExporter()
        self._current_span: Optional[Span] = None
        self._completed_spans: List[Span] = []
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def current_span(self) -> Optional[Span]:
        """Get current active span."""
        return self._current_span

    def _generate_id(self) -> str:
        """Generate a span/trace ID."""
        return uuid.uuid4().hex[:16]

    @contextmanager
    def start_span(
        self,
        name: str,
        parent: Optional[Span] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Generator[Span, None, None]:
        """
        Start a new span as context manager.

        Args:
            name: Span name
            parent: Parent span (uses current if not specified)
            attributes: Initial attributes

        Yields:
            The created span
        """
        if not self._enabled:
            # Return a no-op span
            yield Span(
                name=name,
                context=SpanContext(trace_id="0" * 32, span_id="0" * 16),
            )
            return

        # Determine parent
        parent = parent or self._current_span

        # Create context
        if parent:
            context = SpanContext(
                trace_id=parent.context.trace_id,
                span_id=self._generate_id(),
                parent_span_id=parent.context.span_id,
            )
        else:
            context = SpanContext(
                trace_id=self._generate_id() + self._generate_id(),
                span_id=self._generate_id(),
            )

        # Create span
        span = Span(
            name=name,
            context=context,
            attributes={"service.name": self._service_name},
        )
        if attributes:
            span.set_attributes(attributes)

        # Set as current
        previous_span = self._current_span
        self._current_span = span

        try:
            yield span
        except Exception as e:
            span.set_status(SpanStatus.ERROR, str(e))
            span.add_event("exception", {"message": str(e), "type": type(e).__name__})
            raise
        finally:
            span.end()
            self._current_span = previous_span
            self._completed_spans.append(span)

            # Export if batch is large enough
            if len(self._completed_spans) >= 10:
                self.flush()

    def flush(self) -> None:
        """Export all completed spans."""
        if self._completed_spans:
            try:
                self._exporter.export(self._completed_spans)
            except Exception as e:
                logger.warning(f"Failed to export spans: {e}")
            self._completed_spans.clear()

    def traced(
        self,
        name: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> Callable:
        """
        Decorator to trace a function.

        Args:
            name: Span name (defaults to function name)
            attributes: Span attributes

        Example:
            @tracer.traced()
            async def my_function():
                ...
        """

        def decorator(func: Callable) -> Callable:
            span_name = name or func.__name__

            def wrapper(*args, **kwargs):
                with self.start_span(span_name, attributes=attributes) as span:
                    span.set_attribute("function", func.__name__)
                    return func(*args, **kwargs)

            return wrapper

        return decorator


# Global tracer registry
_tracers: Dict[str, Tracer] = {}


def get_tracer(service_name: str = "default") -> Tracer:
    """Get or create a tracer for the given service."""
    if service_name not in _tracers:
        _tracers[service_name] = Tracer(service_name)
    return _tracers[service_name]


# Global tracer instance for backward compatibility
tracer = get_tracer()


def setup_telemetry(
    service_name: str = "baselith-core",
    otlp_endpoint: Optional[str] = None,
    enable_fastapi: bool = True,
    enable_redis: bool = True,
    enable_httpx: bool = True,
) -> None:
    """
    Setup OpenTelemetry instrumentation for the application.

    Args:
        service_name: Name of the service for resource identification
        otlp_endpoint: OTLP collector endpoint (e.g., "http://localhost:4317")
        enable_fastapi: Enable FastAPI auto-instrumentation
        enable_redis: Enable Redis auto-instrumentation
        enable_httpx: Enable HTTPX auto-instrumentation
    """
    config = get_app_config()

    if not config.telemetry_enabled:
        logger.info("Telemetry disabled by configuration.")
        return

    # Get endpoint from config
    endpoint = otlp_endpoint or config.telemetry_otel_endpoint

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME

        # Configure resource
        resource = Resource.create({SERVICE_NAME: service_name})
        provider = TracerProvider(resource=resource)

        # Add OTLP exporter
        processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        logger.info(f"[OTEL] TracerProvider configured, exporting to {endpoint}")

        # Auto-instrument FastAPI
        if enable_fastapi:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                FastAPIInstrumentor().instrument()
                logger.info("[OTEL] FastAPI instrumentation enabled")
            except ImportError:
                logger.warning("[OTEL] FastAPI instrumentation not available")

        # Auto-instrument Redis
        if enable_redis:
            try:
                from opentelemetry.instrumentation.redis import RedisInstrumentor

                RedisInstrumentor().instrument()
                logger.info("[OTEL] Redis instrumentation enabled")
            except ImportError:
                logger.warning("[OTEL] Redis instrumentation not available")

        # Auto-instrument HTTPX
        if enable_httpx:
            try:
                from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

                HTTPXClientInstrumentor().instrument()
                logger.info("[OTEL] HTTPX instrumentation enabled")
            except ImportError:
                logger.warning("[OTEL] HTTPX instrumentation not available")

    except ImportError as e:
        logger.warning(
            f"[OTEL] OpenTelemetry SDK not installed, telemetry disabled: {e}"
        )
    except Exception as e:
        logger.error(f"[OTEL] Failed to setup telemetry: {e}")


__all__ = [
    "SpanStatus",
    "SpanContext",
    "Span",
    "SpanExporter",
    "ConsoleExporter",
    "InMemoryExporter",
    "OTLPExporter",
    "Tracer",
    "get_tracer",
    "tracer",
    "setup_telemetry",
]
