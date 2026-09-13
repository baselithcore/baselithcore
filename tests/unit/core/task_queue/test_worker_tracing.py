"""The worker's CONSUMER span and its tenant binding.

``perform_job`` is the seam where a queued job becomes running code: it is the
only place that can both restore the identity the job was enqueued with and
open a span that joins the enqueuer's trace.
"""

from __future__ import annotations

import types
from unittest.mock import patch

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from rq import Worker

from core.task_queue.trace_context import inject_trace_context
from core.task_queue.worker import TenantAwareWorker

pytestmark = [pytest.mark.unit]


@pytest.fixture
def otel_sdk(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", lambda *a, **k: provider.get_tracer("t"))
    yield exporter, provider


@pytest.fixture
def worker():
    """A TenantAwareWorker without RQ's Redis-touching constructor."""
    return object.__new__(TenantAwareWorker)


def _job(meta=None, job_id="job-1", func="pkg.mod.fn"):
    return types.SimpleNamespace(
        id=job_id, func_name=func, origin="documents", meta=meta or {}
    )


def _queue(name="documents"):
    return types.SimpleNamespace(name=name)


class TestConsumerSpan:
    def test_a_consumer_span_wraps_the_job(self, worker, otel_sdk):
        exporter, _provider = otel_sdk
        with patch.object(Worker, "perform_job", return_value="done") as base:
            assert worker.perform_job(_job(), _queue()) == "done"
        base.assert_called_once()
        (span,) = exporter.get_finished_spans()
        assert span.kind is trace.SpanKind.CONSUMER
        assert span.attributes["messaging.destination.name"] == "documents"

    def test_span_joins_the_enqueuers_trace(self, worker, otel_sdk):
        exporter, provider = otel_sdk
        meta: dict = {"tenant_id": "acme"}
        with provider.get_tracer("t").start_as_current_span("produce") as producer:
            inject_trace_context(meta)
            expected = producer.get_span_context().trace_id

        with patch.object(Worker, "perform_job", return_value=None):
            worker.perform_job(_job(meta=meta), _queue())

        consumer = next(
            s for s in exporter.get_finished_spans() if s.name == "process documents"
        )
        assert consumer.context.trace_id == expected

    def test_queue_without_a_name_falls_back_to_job_origin(self, worker, otel_sdk):
        exporter, _provider = otel_sdk
        with patch.object(Worker, "perform_job", return_value=None):
            worker.perform_job(_job(), object())
        (span,) = exporter.get_finished_spans()
        assert span.attributes["messaging.destination.name"] == "documents"

    def test_job_failure_propagates(self, worker, otel_sdk):
        exporter, _provider = otel_sdk
        with patch.object(Worker, "perform_job", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                worker.perform_job(_job(), _queue())
        (span,) = exporter.get_finished_spans()
        assert span.status.status_code is trace.StatusCode.ERROR


class TestTenantBinding:
    def test_tenant_from_meta_is_bound_during_the_job(self, worker):
        seen = {}

        def _capture(*_args, **_kwargs):
            from core.context import get_current_tenant_id

            seen["tenant"] = get_current_tenant_id()

        with patch.object(Worker, "perform_job", side_effect=_capture):
            worker.perform_job(_job(meta={"tenant_id": "acme"}), _queue())
        assert seen["tenant"] == "acme"

    def test_tenant_context_is_restored_afterwards(self, worker):
        from core.context import get_current_tenant_id

        before = get_current_tenant_id()
        with patch.object(Worker, "perform_job", return_value=None):
            worker.perform_job(_job(meta={"tenant_id": "acme"}), _queue())
        assert get_current_tenant_id() == before

    def _tenant_during(self, worker):
        seen = {}

        def _capture(*_args, **_kwargs):
            from core.context import get_current_tenant_id

            seen["tenant"] = get_current_tenant_id()

        with patch.object(Worker, "perform_job", side_effect=_capture):
            worker.perform_job(_job(meta={}), _queue())
        return seen["tenant"]

    def test_tenantless_job_under_rls_runs_as_the_system_tenant(
        self, worker, monkeypatch
    ):
        """With RLS on, ``default`` is another tenant's rows — a job that names
        no tenant must declare itself instead of inheriting one."""
        from core.db.connection import SYSTEM_TENANT_ID

        monkeypatch.setattr("core.task_queue.worker._rls_enabled", lambda: True)
        assert self._tenant_during(worker) == SYSTEM_TENANT_ID

    def test_tenantless_job_without_rls_keeps_the_default_fallback(
        self, worker, monkeypatch
    ):
        """Nothing reads app.tenant_id for access control when RLS is off, so
        the historical namespace must not shift under existing deployments."""
        monkeypatch.setattr("core.task_queue.worker._rls_enabled", lambda: False)
        assert self._tenant_during(worker) == "default"

    def test_missing_system_scope_helper_is_tolerated(self, worker, monkeypatch):
        """The helper is optional: an older core must still run jobs."""
        monkeypatch.setattr("core.task_queue.worker._rls_enabled", lambda: True)
        monkeypatch.setattr("core.task_queue.worker._system_tenant_scope", lambda: None)
        with patch.object(Worker, "perform_job", return_value="ok"):
            assert worker.perform_job(_job(meta={}), _queue()) == "ok"
        assert self._tenant_during(worker) == "default"
