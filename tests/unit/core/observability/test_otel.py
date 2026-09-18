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
from core.observability.otel_exporters import PROTOCOL_GRPC
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


class TestOptionalExport:
    """An empty endpoint installs the providers without an OTLP exporter.

    A collector is deployment-optional: the in-process span sinks (dashboards,
    debug readers) are fed by the bridge processor, not by the exporter. An
    exporter aimed at an endpoint nothing listens on would retry every batch
    forever, so it must not be attached at all.
    """

    @staticmethod
    def _span_processor_types(provider) -> list[str]:
        """Names of the processor classes registered on *provider*."""
        multi = provider._active_span_processor
        return [type(p).__name__ for p in multi._span_processors]

    @pytest.mark.parametrize("endpoint", ["", "   ", None])
    def test_blank_endpoint_means_no_collector(self, endpoint):
        assert otel._normalize_endpoint(endpoint) is None

    def test_endpoint_is_stripped(self):
        assert otel._normalize_endpoint("  http://c:4317 ") == "http://c:4317"

    def test_no_exporter_without_endpoint(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            trace, "set_tracer_provider", lambda p: captured.setdefault("p", p)
        )
        provider = otel._setup_tracing(
            resource=None,
            endpoint=None,
            protocol=PROTOCOL_GRPC,
            sampler=otel._build_sampler(1.0),
            console_export=False,
        )
        names = self._span_processor_types(provider)
        assert "BatchSpanProcessor" not in names
        # The in-process mirror is the whole point of the collector-less mode.
        assert "_SinkSpanProcessor" in names

    def test_exporter_attached_with_endpoint(self, monkeypatch):
        monkeypatch.setattr(trace, "set_tracer_provider", lambda p: None)
        provider = otel._setup_tracing(
            resource=None,
            endpoint="http://collector:4317",
            protocol=PROTOCOL_GRPC,
            sampler=otel._build_sampler(1.0),
            console_export=False,
        )
        assert "BatchSpanProcessor" in self._span_processor_types(provider)
        provider.shutdown()

    def test_meter_provider_skipped_with_no_destination(self):
        assert otel._setup_metrics(None, None, PROTOCOL_GRPC, False) is None


class TestExistingAppIsInstrumented:
    """The app exists before telemetry starts — it must still be traced.

    ``FastAPIInstrumentor().instrument()`` swaps the ``fastapi.FastAPI`` class,
    so it only reaches apps built afterwards. Telemetry is initialized from the
    lifespan, which runs once the app object exists, so the global patch missed
    the app actually serving traffic: instrumentation logged itself as enabled
    and produced no HTTP server span at all.
    """

    def test_prebuilt_app_gets_instrumented(self):
        from fastapi import FastAPI

        from core.observability import otel_instrumentation

        app = FastAPI()
        assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
        otel_instrumentation._instrument_app(app)
        assert getattr(app, "_is_instrumented_by_opentelemetry", False) is True

    def test_probe_and_sub_span_exclusions_reach_the_instrumentor(self, monkeypatch):
        """Probes stay untraced and one request stays one span."""
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        from core.observability import otel_instrumentation

        monkeypatch.delenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", raising=False)
        monkeypatch.delenv("OTEL_PYTHON_EXCLUDED_URLS", raising=False)
        monkeypatch.delenv("BASELITH_OTEL_ASGI_SUB_SPANS", raising=False)

        # Resolved before the spy replaces instrument_app: the helper probes
        # that very signature to decide whether exclude_spans is supported.
        expected_spans = otel_instrumentation._fastapi_exclude_spans()
        assert expected_spans == ["receive", "send"]

        seen: dict = {}

        def spy(app, **kwargs):
            seen.update(kwargs)

        monkeypatch.setattr(
            otel_instrumentation,
            "_fastapi_exclude_spans",
            lambda: expected_spans,
        )
        monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", staticmethod(spy))
        otel_instrumentation._instrument_app(object())

        assert "/health$" in seen["excluded_urls"]
        assert seen["exclude_spans"] == ["receive", "send"]

    def test_sub_spans_can_be_restored_for_debugging(self, monkeypatch):
        from core.observability import otel_instrumentation

        monkeypatch.setenv("BASELITH_OTEL_ASGI_SUB_SPANS", "true")
        assert otel_instrumentation._fastapi_exclude_spans() is None

    def test_instrument_app_is_a_noop_without_an_app(self):
        from core.observability import otel_instrumentation

        otel_instrumentation._instrument_app(None)  # must not raise


class TestInstrumentationOrdering:
    """*When* the app is instrumented decides whether it is traced at all.

    Starlette builds its middleware stack lazily and caches it on the first
    call into the application — and the lifespan startup message is such a
    call. The FastAPI instrumentation works by wrapping
    ``build_middleware_stack``, so instrumenting from the lifespan patches a
    function that will never run again: telemetry reports itself enabled and
    produces no HTTP server span at all. These two tests pin both halves so the
    call cannot drift back into the lifespan.
    """

    @staticmethod
    def _app_with_route():
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/work")
        def work() -> dict[str, str]:
            return {"ok": "yes"}

        return app

    @staticmethod
    def _recording_provider():
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        return exporter, provider

    def test_instrumented_before_first_call_emits_a_server_span(self):
        from fastapi.testclient import TestClient
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        app = self._app_with_route()
        exporter, provider = self._recording_provider()

        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        TestClient(app).get("/work")

        kinds = [(s.name, s.kind.name) for s in exporter.get_finished_spans()]
        assert any(k == "SERVER" for _, k in kinds), kinds

    def test_instrumented_after_the_stack_is_frozen_emits_nothing(self):
        """The bug this ordering exists to avoid — kept as an executable note."""
        from fastapi.testclient import TestClient
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        app = self._app_with_route()
        exporter, provider = self._recording_provider()

        client = TestClient(app)
        client.get("/work")  # first call: Starlette freezes the stack here
        FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
        client.get("/work")

        kinds = [s.kind.name for s in exporter.get_finished_spans()]
        assert "SERVER" not in kinds, kinds
