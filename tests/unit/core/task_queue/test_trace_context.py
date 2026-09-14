"""W3C trace propagation from the enqueuer into the RQ worker.

A job used to start a brand-new trace in the worker, so "the request was slow"
and "the background job it kicked off failed" were two unrelated traces with
nothing linking them. These tests pin the carrier keys and the CONSUMER span.
"""

from __future__ import annotations

import types

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from core.task_queue.trace_context import (
    TRACE_CARRIER_KEYS,
    consumer_span,
    extract_trace_context,
    inject_trace_context,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture
def otel_sdk(monkeypatch):
    """Local in-memory provider; never touches the process-global one."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda *a, **k: provider.get_tracer("t"))
    yield exporter, provider


def _job(job_id="job-1", meta=None, func="pkg.mod.fn", origin="documents"):
    return types.SimpleNamespace(
        id=job_id,
        func_name=func,
        origin=origin,
        meta=meta if meta is not None else {},
    )


class TestInject:
    def test_traceparent_added_inside_an_active_span(self, otel_sdk):
        _exporter, provider = otel_sdk
        meta: dict = {}
        with provider.get_tracer("t").start_as_current_span("produce"):
            inject_trace_context(meta)
        assert "traceparent" in meta
        assert meta["traceparent"].startswith("00-")

    def test_nothing_added_without_an_active_span(self):
        meta: dict = {}
        inject_trace_context(meta)
        assert meta == {}

    def test_existing_meta_is_preserved(self, otel_sdk):
        _exporter, provider = otel_sdk
        meta = {"tenant_id": "acme"}
        with provider.get_tracer("t").start_as_current_span("produce"):
            inject_trace_context(meta)
        assert meta["tenant_id"] == "acme"

    def test_returns_the_same_mapping(self):
        meta: dict = {}
        assert inject_trace_context(meta) is meta

    def test_carrier_keys_are_the_w3c_ones(self):
        assert "traceparent" in TRACE_CARRIER_KEYS
        assert "tracestate" in TRACE_CARRIER_KEYS


class TestExtract:
    def test_none_without_a_carrier(self):
        assert extract_trace_context({}) is None
        assert extract_trace_context({"tenant_id": "acme"}) is None

    def test_context_returned_for_a_traceparent(self):
        carrier = {
            "traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
        }
        assert extract_trace_context(carrier) is not None

    def test_malformed_carrier_never_raises(self):
        assert extract_trace_context({"traceparent": "garbage"}) is not None


class TestConsumerSpan:
    def test_span_is_recorded_with_consumer_kind(self, otel_sdk):
        exporter, _provider = otel_sdk
        with consumer_span(_job(), "documents"):
            pass
        (span,) = exporter.get_finished_spans()
        assert span.kind is trace.SpanKind.CONSUMER
        assert span.name == "process documents"

    def test_messaging_attributes(self, otel_sdk):
        exporter, _provider = otel_sdk
        with consumer_span(_job(job_id="j9", func="pkg.mod.fn"), "analysis"):
            pass
        (span,) = exporter.get_finished_spans()
        assert span.attributes["messaging.system"] == "rq"
        assert span.attributes["messaging.operation.name"] == "process"
        assert span.attributes["messaging.destination.name"] == "analysis"
        assert span.attributes["messaging.message.id"] == "j9"
        assert span.attributes["code.function.name"] == "pkg.mod.fn"

    def test_continues_the_enqueuers_trace(self, otel_sdk):
        exporter, provider = otel_sdk
        meta: dict = {}
        with provider.get_tracer("t").start_as_current_span("produce") as producer:
            inject_trace_context(meta)
            producer_trace_id = producer.get_span_context().trace_id

        with consumer_span(_job(meta=meta), "documents"):
            pass

        consumer = next(
            s for s in exporter.get_finished_spans() if s.name == "process documents"
        )
        assert consumer.context.trace_id == producer_trace_id
        assert consumer.parent is not None

    def test_starts_a_new_trace_without_a_carrier(self, otel_sdk):
        exporter, _provider = otel_sdk
        with consumer_span(_job(), "documents"):
            pass
        (span,) = exporter.get_finished_spans()
        assert span.parent is None

    def test_exception_is_recorded_and_reraised(self, otel_sdk):
        exporter, _provider = otel_sdk
        with pytest.raises(ValueError):
            with consumer_span(_job(), "documents"):
                raise ValueError("boom")
        (span,) = exporter.get_finished_spans()
        assert span.status.status_code is trace.StatusCode.ERROR

    def test_yields_none_when_otel_is_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "core.task_queue.trace_context._tracer", lambda: None, raising=False
        )
        with consumer_span(_job(), "documents") as span:
            assert span is None

    def test_a_broken_tracer_never_breaks_the_job(self, monkeypatch):
        def _boom(*_args, **_kwargs):
            raise RuntimeError("tracer exploded")

        monkeypatch.setattr(trace, "get_tracer", _boom)
        with consumer_span(_job(), "documents") as span:
            assert span is None
