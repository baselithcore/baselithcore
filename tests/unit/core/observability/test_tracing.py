"""
Unit tests for tracing system.
"""

import pytest
from core.observability.tracing import (
    InMemoryExporter,
    Span,
    SpanContext,
    SpanStatus,
    Tracer,
    get_tracer,
)


class TestSpanContext:
    """Tests for SpanContext."""

    def test_to_headers(self):
        """Convert to W3C trace context headers."""
        ctx = SpanContext(trace_id="abc123", span_id="def456")
        headers = ctx.to_headers()

        assert "traceparent" in headers
        assert "abc123" in headers["traceparent"]
        assert "def456" in headers["traceparent"]

    def test_from_headers(self):
        """Parse from W3C headers."""
        headers = {"traceparent": "00-abc123def456-span789-01"}
        ctx = SpanContext.from_headers(headers)

        assert ctx is not None
        assert ctx.trace_id == "abc123def456"
        assert ctx.span_id == "span789"

    def test_from_headers_missing(self):
        """Missing headers returns None."""
        assert SpanContext.from_headers({}) is None

    def test_from_headers_invalid(self):
        """Invalid format returns None."""
        assert SpanContext.from_headers({"traceparent": "invalid"}) is None


class TestSpan:
    """Tests for Span."""

    def test_set_attribute(self):
        """Set single attribute."""
        span = Span(name="test", context=SpanContext("t", "s"))
        span.set_attribute("key", "value")

        assert span.attributes["key"] == "value"

    def test_set_attributes(self):
        """Set multiple attributes."""
        span = Span(name="test", context=SpanContext("t", "s"))
        span.set_attributes({"a": 1, "b": 2})

        assert span.attributes["a"] == 1
        assert span.attributes["b"] == 2

    def test_add_event(self):
        """Add event to span."""
        span = Span(name="test", context=SpanContext("t", "s"))
        span.add_event("checkpoint", {"step": 1})

        assert len(span.events) == 1
        assert span.events[0]["name"] == "checkpoint"

    def test_set_status(self):
        """Set span status."""
        span = Span(name="test", context=SpanContext("t", "s"))
        span.set_status(SpanStatus.ERROR, "Something failed")

        assert span.status == SpanStatus.ERROR
        assert span.attributes["status.description"] == "Something failed"

    def test_end(self):
        """End sets end_time and default status."""
        span = Span(name="test", context=SpanContext("t", "s"))
        span.end()

        assert span.end_time is not None
        assert span.status == SpanStatus.OK

    def test_duration_ms(self):
        """Duration calculated in milliseconds."""
        span = Span(name="test", context=SpanContext("t", "s"))
        span.end()

        assert span.duration_ms is not None
        assert span.duration_ms >= 0

    def test_to_dict(self):
        """Convert span to dictionary."""
        span = Span(name="test", context=SpanContext("trace1", "span1"))
        span.end()
        data = span.to_dict()

        assert data["name"] == "test"
        assert data["trace_id"] == "trace1"
        assert data["span_id"] == "span1"


class TestInMemoryExporter:
    """Tests for InMemoryExporter."""

    def test_export_stores_spans(self):
        """Export stores spans in memory."""
        exporter = InMemoryExporter()
        span = Span(name="test", context=SpanContext("t", "s"))

        exporter.export([span])

        assert len(exporter.spans) == 1

    def test_clear(self):
        """Clear removes all spans."""
        exporter = InMemoryExporter()
        span = Span(name="test", context=SpanContext("t", "s"))
        exporter.export([span])

        exporter.clear()

        assert len(exporter.spans) == 0


class TestTracer:
    """Tests for Tracer."""

    def test_start_span_context_manager(self):
        """start_span creates and ends span."""
        exporter = InMemoryExporter()
        tracer = Tracer("test-service", exporter=exporter)

        with tracer.start_span("operation") as span:
            span.set_attribute("key", "value")

        tracer.flush()
        assert len(exporter.spans) == 1
        assert exporter.spans[0].name == "operation"
        assert exporter.spans[0].attributes["key"] == "value"

    def test_nested_spans(self):
        """Nested spans have parent context."""
        exporter = InMemoryExporter()
        tracer = Tracer("test-service", exporter=exporter)

        with tracer.start_span("parent") as parent:
            with tracer.start_span("child") as child:
                assert child.context.parent_span_id == parent.context.span_id
                assert child.context.trace_id == parent.context.trace_id

    def test_span_captures_exception(self):
        """Span captures exception and sets error status."""
        exporter = InMemoryExporter()
        tracer = Tracer("test-service", exporter=exporter)

        with pytest.raises(ValueError):
            with tracer.start_span("failing") as _span:
                raise ValueError("test error")

        tracer.flush()
        assert exporter.spans[0].status == SpanStatus.ERROR
        assert len(exporter.spans[0].events) == 1
        assert exporter.spans[0].events[0]["name"] == "exception"

    def test_disabled_tracer(self):
        """Disabled tracer creates no-op spans."""
        exporter = InMemoryExporter()
        tracer = Tracer("test-service", exporter=exporter)
        tracer.enabled = False

        with tracer.start_span("operation") as _span:
            pass

        tracer.flush()
        assert len(exporter.spans) == 0

    def test_traced_decorator(self):
        """@traced decorator creates span."""
        exporter = InMemoryExporter()
        tracer = Tracer("test-service", exporter=exporter)

        @tracer.traced()
        def my_function():
            return 42

        result = my_function()

        tracer.flush()
        assert result == 42
        assert len(exporter.spans) == 1
        assert exporter.spans[0].name == "my_function"

    def test_traced_with_custom_name(self):
        """@traced with custom name."""
        exporter = InMemoryExporter()
        tracer = Tracer("test-service", exporter=exporter)

        @tracer.traced(name="custom-operation")
        def my_function():
            pass

        my_function()

        tracer.flush()
        assert exporter.spans[0].name == "custom-operation"

    def test_current_span(self):
        """current_span returns active span."""
        tracer = Tracer("test")

        assert tracer.current_span is None

        with tracer.start_span("op") as span:
            assert tracer.current_span is span

        assert tracer.current_span is None


class TestGetTracer:
    """Tests for get_tracer factory."""

    def test_returns_tracer(self):
        """get_tracer returns Tracer instance."""
        tracer = get_tracer("my-service")
        assert isinstance(tracer, Tracer)

    def test_same_service_same_tracer(self):
        """Same service name returns same tracer."""
        tracer1 = get_tracer("service-a")
        tracer2 = get_tracer("service-a")
        assert tracer1 is tracer2

    def test_different_services(self):
        """Different service names create different tracers."""
        tracer1 = get_tracer("service-a")
        tracer2 = get_tracer("service-b")
        assert tracer1 is not tracer2
