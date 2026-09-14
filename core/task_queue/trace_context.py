"""W3C trace-context propagation across the RQ queue boundary.

A queued job runs minutes later in another process, and until now it started a
trace of its own. So the request that enqueued it and the work it caused were
two unrelated traces: "checkout was slow" and "the indexing job failed" could
not be joined, and a failure in the background was invisible from the span that
asked for it.

This module carries the standard W3C ``traceparent``/``tracestate`` (plus
``baggage``) in the job's metadata — the same carrier HTTP uses, so a collector,
a sampling decision and a trace viewer all treat the two halves as one trace:

* producers call :func:`inject_trace_context` when building job meta
  (:func:`core.task_queue.scheduler.ambient_job_meta` does it for every enqueue);
* the worker wraps execution in :func:`consumer_span`, a ``CONSUMER`` span
  parented on the carrier and annotated with OTel messaging attributes.

Everything here is best-effort. A missing OTel SDK, a broken tracer or a
malformed carrier degrades to "no span" — never to a failed job.
"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Carrier keys copied between job metadata and the OTel propagators. These are
#: the W3C names on purpose: anything else reading the queue (a dashboard, a
#: non-Python consumer) recognises them without knowing about this framework.
TRACE_CARRIER_KEYS: tuple[str, ...] = ("traceparent", "tracestate", "baggage")

#: Instrumentation scope name for the spans produced here.
INSTRUMENTATION_NAME = "core.task_queue"

#: OTel messaging semantic-convention attribute keys, spelled out so this
#: module does not pin a semconv package version.
_ATTR_SYSTEM = "messaging.system"
_ATTR_OPERATION_NAME = "messaging.operation.name"
_ATTR_OPERATION_TYPE = "messaging.operation.type"
_ATTR_DESTINATION = "messaging.destination.name"
_ATTR_MESSAGE_ID = "messaging.message.id"
_ATTR_CODE_FUNCTION = "code.function.name"


def inject_trace_context(
    meta: MutableMapping[str, Any] | None = None,
) -> MutableMapping[str, Any]:
    """Write the active trace context into *meta* as W3C carrier keys.

    Args:
        meta: Job metadata to enrich in place. A new dict is created when
            omitted.

    Returns:
        The same mapping, with ``traceparent`` (and ``tracestate``/``baggage``
        when present) set. Unchanged when no span is active — an invalid
        context must not be propagated, or the worker would parent its span on
        a trace that does not exist.
    """
    target: MutableMapping[str, Any] = {} if meta is None else meta
    try:
        from opentelemetry.propagate import inject

        carrier: dict[str, str] = {}
        inject(carrier)
        for key in TRACE_CARRIER_KEYS:
            value = carrier.get(key)
            if value:
                target[key] = value
    except Exception as exc:  # telemetry must never block an enqueue
        logger.debug("Trace context injection skipped: %s", exc)
    return target


def extract_trace_context(meta: MutableMapping[str, Any] | None) -> Any | None:
    """Rebuild the OTel ``Context`` carried by *meta*, or ``None``.

    Args:
        meta: Job metadata as stored by :func:`inject_trace_context`.

    Returns:
        An OTel ``Context`` to use as the parent, or ``None`` when the job
        carries no trace context (so the worker starts its own trace).
    """
    if not meta:
        return None
    carrier = {
        key: str(meta[key]) for key in TRACE_CARRIER_KEYS if meta.get(key) is not None
    }
    if not carrier:
        return None
    try:
        from opentelemetry.propagate import extract

        return extract(carrier)
    except Exception as exc:  # a malformed carrier is not a reason to fail
        logger.debug("Trace context extraction failed: %s", exc)
        return None


def _tracer() -> Any | None:
    """Return the OTel tracer for this scope, or ``None`` when unavailable."""
    try:
        from opentelemetry import trace as otel_trace

        return otel_trace.get_tracer(INSTRUMENTATION_NAME)
    except Exception as exc:  # pragma: no cover - exercised via consumer_span
        logger.debug("OpenTelemetry tracer unavailable: %s", exc)
        return None


def job_span_attributes(job: Any, queue_name: str) -> dict[str, Any]:
    """OTel messaging attributes describing one job execution.

    Deliberately free of payload: job arguments can carry user content and
    credentials, and a span is not an audit record.
    """
    attributes: dict[str, Any] = {
        _ATTR_SYSTEM: "rq",
        _ATTR_OPERATION_NAME: "process",
        _ATTR_OPERATION_TYPE: "process",
        _ATTR_DESTINATION: queue_name,
    }
    job_id = getattr(job, "id", None)
    if job_id:
        attributes[_ATTR_MESSAGE_ID] = str(job_id)
    func_name = getattr(job, "func_name", None)
    if func_name:
        attributes[_ATTR_CODE_FUNCTION] = str(func_name)
    return attributes


@contextmanager
def consumer_span(job: Any, queue_name: str) -> Iterator[Any]:
    """Open a ``CONSUMER`` span around the execution of *job*.

    The span continues the enqueuer's trace when the job's metadata carries a
    ``traceparent``, and starts a fresh one otherwise.

    Args:
        job: The RQ job about to run (read for ``id``, ``func_name``, ``meta``).
        queue_name: Queue the job came off, used as the span's destination.

    Yields:
        The OTel span, or ``None`` when tracing is unavailable — so callers
        must not assume a span object exists.
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return

    try:
        from opentelemetry import trace as otel_trace

        span_cm = tracer.start_as_current_span(
            f"process {queue_name}",
            context=extract_trace_context(getattr(job, "meta", None)),
            kind=otel_trace.SpanKind.CONSUMER,
            attributes=job_span_attributes(job, queue_name),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Consumer span not started: %s", exc)
        yield None
        return

    with span_cm as span:
        yield span


__all__ = [
    "INSTRUMENTATION_NAME",
    "TRACE_CARRIER_KEYS",
    "consumer_span",
    "extract_trace_context",
    "inject_trace_context",
    "job_span_attributes",
]
