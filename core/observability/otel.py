"""
Centralized OpenTelemetry SDK bootstrap (traces + metrics).

This module is the **single source of truth** for OpenTelemetry provider
configuration. It builds a rich OTel ``Resource``, installs sampled
``TracerProvider``/``MeterProvider`` instances wired to an OTLP collector,
turns on auto-instrumentation for FastAPI/HTTPX/Redis/psycopg, and sets the
W3C propagators. The homegrown ``Tracer`` in
:mod:`core.observability.tracing` bridges into the ``TracerProvider``
configured here, so custom spans reach the collector alongside
auto-instrumentation spans.

Design rules:
- **Idempotent.** ``setup_telemetry`` may be called multiple times; only the
  first call installs providers. ``shutdown_telemetry`` flushes and tears them
  down (registered with ``atexit`` as a safety net).
- **Graceful degradation.** Every OTel import is guarded. A missing SDK or
  instrumentation package downgrades to a warning, never an exception — the
  framework keeps running with tracing disabled.
- **The collector is optional.** An empty ``telemetry_otel_endpoint`` installs
  the providers and the auto-instrumentation *without* an OTLP exporter. Spans
  are still produced and still reach in-process consumers through the sink
  bridge (a control-plane trace viewer, a debug reader), they simply do not
  leave the process. Attaching an exporter pointed at an endpoint nothing is
  listening on is the worst of both worlds: no trace backend *and* a retrying
  gRPC exporter burning CPU and filling the log on every batch.
- **No reverse dependency.** This module imports only ``config``, ``logging``
  and its own ``otel_instrumentation`` sibling; ``tracing.py`` imports *from*
  here (lazily), never the reverse.

The Prometheus ``/metrics`` scrape endpoint (``core.observability.metrics``)
is independent of the OTLP metric push configured here; both can run together.
"""

from __future__ import annotations

import atexit
import os
import socket
import threading
from typing import Any

from core.config import get_app_config
from core.observability.logging import get_logger
from core.observability.otel_instrumentation import (
    instrument_libraries,
    setup_propagators,
)

logger = get_logger(__name__)

# Semantic-convention attribute keys, spelled out as plain strings so we do not
# depend on a specific semconv package version.
_ATTR_SERVICE_NAME = "service.name"
_ATTR_SERVICE_VERSION = "service.version"
_ATTR_SERVICE_NAMESPACE = "service.namespace"
_ATTR_SERVICE_INSTANCE_ID = "service.instance.id"
_ATTR_DEPLOYMENT_ENVIRONMENT = "deployment.environment"

_lock = threading.Lock()
_initialized = False
_tracer_provider: Any = None
_meter_provider: Any = None


def is_initialized() -> bool:
    """Return ``True`` once a real OTel TracerProvider has been installed."""
    return _initialized and _tracer_provider is not None


def _normalize_endpoint(value: str | None) -> str | None:
    """Return a usable OTLP endpoint, or ``None`` when none is configured.

    Blank and whitespace-only values mean "no collector" rather than "export to
    the empty string": a Helm chart or a ``.env`` that leaves the key present
    but empty must not produce an exporter aimed at nothing.
    """
    endpoint = (value or "").strip()
    return endpoint or None


def _build_resource(service_name: str, config: Any) -> Any:
    """Construct an OTel ``Resource`` with rich service identity attributes."""
    from opentelemetry.sdk.resources import Resource

    attributes: dict[str, Any] = {
        _ATTR_SERVICE_NAME: service_name,
        _ATTR_SERVICE_VERSION: getattr(config, "service_version", "0.0.0"),
        _ATTR_SERVICE_NAMESPACE: "baselith",
        _ATTR_SERVICE_INSTANCE_ID: f"{socket.gethostname()}:{os.getpid()}",
        _ATTR_DEPLOYMENT_ENVIRONMENT: getattr(
            config, "deployment_environment", "development"
        ),
    }
    # Resource.create merges in OTEL_RESOURCE_ATTRIBUTES env + SDK attrs.
    return Resource.create(attributes)


def _build_sampler(sample_rate: float) -> Any:
    """Return a ParentBased(TraceIdRatio) sampler clamped to [0, 1]."""
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )

    rate = max(0.0, min(1.0, sample_rate))
    if rate >= 1.0:
        return ParentBased(root=ALWAYS_ON)
    return ParentBased(root=TraceIdRatioBased(rate))


def _setup_tracing(
    resource: Any,
    endpoint: str | None,
    sampler: Any,
    console_export: bool,
) -> Any:
    """Install a TracerProvider with optional OTLP and console export.

    ``endpoint`` of ``None`` installs the provider with no OTLP exporter: spans
    are sampled, instrumented and handed to in-process sinks, but nothing is
    shipped off-box.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider(resource=resource, sampler=sampler)

    if endpoint is not None:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint))
        )

    if console_export:
        from opentelemetry.sdk.trace.export import (
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    # In-process mirror: hand every finished span to locally registered sinks
    # (dashboards, debug readers) alongside the OTLP export. No-op when nobody
    # is listening; never fails setup.
    from core.observability.span_bridge import install_span_sink_bridge

    install_span_sink_bridge(provider)

    trace.set_tracer_provider(provider)
    logger.info(
        "[OTEL] TracerProvider installed (export=%s)",
        endpoint if endpoint is not None else "in-process only",
    )
    return provider


def _setup_metrics(
    resource: Any, endpoint: str | None, console_export: bool
) -> Any | None:
    """Install a MeterProvider with OTLP periodic metric export.

    Returns ``None`` when there is no destination at all (no endpoint, no
    console): unlike traces, OTel metrics have no in-process consumer here —
    the Prometheus ``/metrics`` scrape is a separate pipeline — so a provider
    whose only reader exports nowhere is a periodic export thread doing pure
    waste.
    """
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    readers: list[Any] = []

    if endpoint is not None:
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter,
        )

        readers.append(
            PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
        )

    if console_export:
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))

    if not readers:
        logger.info("[OTEL] MeterProvider skipped (no OTLP endpoint, no console)")
        return None

    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)
    logger.info(
        "[OTEL] MeterProvider installed (export=%s)",
        endpoint if endpoint is not None else "console only",
    )
    return provider


def setup_telemetry(
    service_name: str = "baselith-core",
    otlp_endpoint: str | None = None,
    *,
    enable_fastapi: bool = True,
    enable_redis: bool = True,
    enable_httpx: bool = True,
    app: Any = None,
) -> bool:
    """
    Configure OpenTelemetry tracing and metrics for the application.

    Idempotent: the first successful call installs the providers; later calls
    are no-ops returning ``True``. Honors ``telemetry_enabled`` and the
    sampling/metrics/console flags from app config.

    Args:
        service_name: Logical service name for the OTel ``Resource``.
        otlp_endpoint: OTLP/gRPC collector endpoint. Falls back to
            ``telemetry_otel_endpoint`` from config. Empty (or blank) means no
            collector: providers and instrumentation are installed, spans stay
            in-process and reach the local sinks, nothing is exported.
        enable_fastapi: Auto-instrument FastAPI.
        enable_redis: Auto-instrument Redis.
        enable_httpx: Auto-instrument the HTTPX client.
        app: The already-constructed FastAPI application, when there is one.
            Pass it: the class-level instrumentation only reaches apps built
            after this call, and telemetry is set up from the lifespan, i.e.
            after the app exists. Without it no HTTP server span is produced.

    Returns:
        ``True`` if telemetry is active after the call, ``False`` otherwise
        (disabled by config or SDK unavailable).
    """
    global _initialized, _tracer_provider, _meter_provider

    config = get_app_config()
    if not getattr(config, "telemetry_enabled", False):
        logger.info("[OTEL] Telemetry disabled by configuration.")
        return False

    with _lock:
        if _initialized:
            return True

        endpoint = _normalize_endpoint(otlp_endpoint or config.telemetry_otel_endpoint)
        console_export = getattr(config, "telemetry_console_export", False)
        sample_rate = getattr(config, "telemetry_traces_sample_rate", 1.0)

        try:
            resource = _build_resource(service_name, config)
            sampler = _build_sampler(sample_rate)
            _tracer_provider = _setup_tracing(
                resource, endpoint, sampler, console_export
            )

            if getattr(config, "telemetry_metrics_enabled", False):
                _meter_provider = _setup_metrics(resource, endpoint, console_export)

            setup_propagators()
            instrument_libraries(enable_fastapi, enable_redis, enable_httpx, app)

            _initialized = True
            atexit.register(shutdown_telemetry)
            logger.info(
                "[OTEL] Telemetry initialized "
                "(service=%s, env=%s, sample_rate=%.2f, export=%s)",
                service_name,
                getattr(config, "deployment_environment", "development"),
                sample_rate,
                endpoint if endpoint is not None else "in-process only",
            )
            return True
        except ImportError as exc:
            logger.warning("[OTEL] SDK not installed, telemetry disabled: %s", exc)
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("[OTEL] Failed to initialize telemetry: %s", exc)

        return False


def shutdown_telemetry() -> None:
    """Flush and tear down OTel providers so no spans/metrics are lost."""
    global _initialized, _tracer_provider, _meter_provider

    with _lock:
        if not _initialized:
            return

        if _tracer_provider is not None:
            try:
                _tracer_provider.shutdown()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("[OTEL] TracerProvider shutdown error: %s", exc)
            _tracer_provider = None

        if _meter_provider is not None:
            try:
                _meter_provider.shutdown()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("[OTEL] MeterProvider shutdown error: %s", exc)
            _meter_provider = None

        _initialized = False
        logger.info("[OTEL] Telemetry shut down.")


__all__ = ["is_initialized", "setup_telemetry", "shutdown_telemetry"]
