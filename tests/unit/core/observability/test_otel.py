"""
Unit tests for the OpenTelemetry backbone and the Tracer→OTel bridge.

These verify that custom (homegrown) spans actually reach the OTel SDK with
their attributes/events/status mirrored, that logs are correlated with the
active trace, and that setup/shutdown behave idempotently and degrade
gracefully.
"""

from __future__ import annotations

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from core.observability import otel
from core.observability.logging import add_otel_context
from core.observability.tracing import SpanStatus, Tracer


@pytest.fixture
def otel_sdk(monkeypatch):
    """Provide an in-memory OTel TracerProvider and force the bridge active.

    Avoids touching the process-global provider (set-once) by patching
    ``opentelemetry.trace.get_tracer`` to draw from a local provider, and
    flagging ``_otel_active`` so ``Tracer`` opens real spans.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    monkeypatch.setattr(trace, "get_tracer", lambda *a, **k: provider.get_tracer("t"))
    monkeypatch.setattr("core.observability.tracing._otel_active", lambda: True)

    yield exporter
    exporter.clear()


class TestTracerBridge:
    """The homegrown Tracer must emit matching OTel spans when active."""

    def test_span_reaches_otel(self, otel_sdk):
        tracer = Tracer("svc")
        with tracer.start_span("operation") as span:
            span.set_attribute("query", "hello")
            span.add_event("checkpoint", {"step": 1})

        spans = otel_sdk.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "operation"
        assert spans[0].attributes["query"] == "hello"
        assert any(e.name == "checkpoint" for e in spans[0].events)

    def test_nested_spans_share_trace(self, otel_sdk):
        tracer = Tracer("svc")
        with tracer.start_span("parent"):
            with tracer.start_span("child"):
                pass

        spans = {s.name: s for s in otel_sdk.get_finished_spans()}
        assert spans["child"].parent is not None
        assert spans["child"].context.trace_id == spans["parent"].context.trace_id

    def test_exception_sets_otel_error(self, otel_sdk):
        tracer = Tracer("svc")
        with pytest.raises(ValueError):
            with tracer.start_span("boom"):
                raise ValueError("nope")

        span = otel_sdk.get_finished_spans()[0]
        from opentelemetry.trace import StatusCode

        assert span.status.status_code == StatusCode.ERROR
        assert any(e.name == "exception" for e in span.events)

    def test_homegrown_state_intact(self, otel_sdk):
        """Bridging must not disturb the homegrown span semantics."""
        tracer = Tracer("svc")
        with tracer.start_span("op") as span:
            span.set_attribute("k", "v")
        assert span.status == SpanStatus.OK
        assert span.attributes["k"] == "v"


class TestNoBridgeWhenInactive:
    """With OTel inactive the Tracer stays purely in-memory (no SDK calls)."""

    def test_no_otel_spans(self, monkeypatch):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        monkeypatch.setattr(
            trace, "get_tracer", lambda *a, **k: provider.get_tracer("t")
        )
        monkeypatch.setattr("core.observability.tracing._otel_active", lambda: False)

        tracer = Tracer("svc")
        with tracer.start_span("op"):
            pass
        assert len(exporter.get_finished_spans()) == 0


class TestLogCorrelation:
    """add_otel_context injects trace_id/span_id from the active span."""

    def test_injects_ids_within_span(self):
        provider = TracerProvider()
        with provider.get_tracer("t").start_as_current_span("op"):
            out = add_otel_context(None, "info", {"event": "hi"})
        assert "trace_id" in out and "span_id" in out
        assert len(out["trace_id"]) == 32
        assert len(out["span_id"]) == 16

    def test_noop_without_span(self):
        out = add_otel_context(None, "info", {"event": "hi"})
        assert "trace_id" not in out
        assert "span_id" not in out


class TestSetupTeardown:
    """setup_telemetry honors config; shutdown is safe."""

    def test_disabled_by_config(self, monkeypatch):
        class _Cfg:
            telemetry_enabled = False

        monkeypatch.setattr(otel, "get_app_config", lambda: _Cfg())
        assert otel.setup_telemetry() is False
        assert otel.is_initialized() is False

    def test_shutdown_noop_when_uninitialized(self):
        # Should not raise even though nothing was set up in this test.
        otel.shutdown_telemetry()
        assert otel.is_initialized() is False


class TestHelpers:
    def test_coerce_attr(self):
        from core.observability.tracing import _coerce_attr

        assert _coerce_attr("s") == "s"
        assert _coerce_attr(3) == 3
        assert _coerce_attr([1, 2]) == [1, 2]
        assert _coerce_attr({"a": 1}) == "{'a': 1}"

    def test_sampler_ratio(self):
        from opentelemetry.sdk.trace.sampling import ParentBased

        assert isinstance(otel._build_sampler(1.0), ParentBased)
        assert isinstance(otel._build_sampler(0.25), ParentBased)
