"""Tests for the in-process span observation seam.

Covers the three guarantees consumers rely on: late registration still sees
spans, a raising sink never breaks the traced operation, and the homegrown
tracer stays silent while the OTel SDK bridge is the active producer (so a
span is never delivered twice).
"""

from __future__ import annotations

from typing import Any

import pytest

from core.observability.span_sink import (
    SpanRecord,
    emit_span,
    has_span_sinks,
    register_span_sink,
    unregister_span_sink,
)
from core.observability.tracing import Tracer


@pytest.fixture
def collected() -> Any:
    """Register a collecting sink and always unregister it afterwards."""
    records: list[SpanRecord] = []
    sink = records.append
    register_span_sink(sink)
    try:
        yield records
    finally:
        unregister_span_sink(sink)


def test_register_is_idempotent_and_removable() -> None:
    records: list[SpanRecord] = []
    register_span_sink(records.append)
    register_span_sink(records.append)  # same bound method object → no dupe
    assert has_span_sinks() is True

    emit_span(SpanRecord(trace_id="t", span_id="s", name="n", start_time=1, end_time=2))
    assert len(records) == 1

    unregister_span_sink(records.append)
    assert has_span_sinks() is False
    unregister_span_sink(records.append)  # no-op, must not raise


def test_tracer_emits_completed_span(collected: list[SpanRecord]) -> None:
    tracer = Tracer("test-service")
    with tracer.start_span("work", attributes={"plugin": "demo"}) as span:
        span.set_attribute("rows", 3)

    assert len(collected) == 1
    record = collected[0]
    assert record.name == "work"
    assert record.service == "test-service"
    assert record.status == "ok"
    assert record.attributes["rows"] == 3
    assert record.duration_ms >= 0


def test_error_span_carries_error_status(collected: list[SpanRecord]) -> None:
    tracer = Tracer("test-service")
    with pytest.raises(ValueError):
        with tracer.start_span("boom"):
            raise ValueError("nope")

    assert collected[-1].status == "error"


def test_child_span_keeps_trace_and_parent(collected: list[SpanRecord]) -> None:
    tracer = Tracer("test-service")
    with tracer.start_span("parent"):
        with tracer.start_span("child"):
            pass

    child, parent = collected[0], collected[1]  # child ends first
    assert child.name == "child" and parent.name == "parent"
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id


def test_raising_sink_never_breaks_the_traced_call() -> None:
    def boom(_record: SpanRecord) -> None:
        raise RuntimeError("sink exploded")

    register_span_sink(boom)
    try:
        tracer = Tracer("test-service")
        with tracer.start_span("work") as span:
            span.set_attribute("ok", True)
    finally:
        unregister_span_sink(boom)


def test_no_homegrown_emit_while_otel_sdk_is_active(
    collected: list[SpanRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the SDK active the bridge processor is the producer — not the Tracer."""
    monkeypatch.setattr("core.observability.tracing._otel_active", lambda: True)
    tracer = Tracer("test-service")
    with tracer.start_span("work"):
        pass

    assert collected == []


def test_span_record_projection_is_json_shaped() -> None:
    record = SpanRecord(
        trace_id="t",
        span_id="s",
        name="n",
        start_time=1.0,
        end_time=1.5,
        attributes={"k": "v"},
    )
    payload = record.to_dict()
    assert payload["duration_ms"] == pytest.approx(500.0)
    assert payload["attributes"] == {"k": "v"}
    assert payload["parent_span_id"] is None
