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
- **No reverse dependency.** This module imports only ``config`` and
  ``logging``; ``tracing.py`` imports *from* here (lazily), never the reverse.

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
    endpoint: str,
    sampler: Any,
    console_export: bool,
) -> Any:
    """Install a TracerProvider with OTLP (and optional console) export."""
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=resource, sampler=sampler)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

    if console_export:
        from opentelemetry.sdk.trace.export import (
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    logger.info("[OTEL] TracerProvider installed (endpoint=%s)", endpoint)
    return provider


def _setup_metrics(resource: Any, endpoint: str, console_export: bool) -> Any:
    """Install a MeterProvider with OTLP periodic metric export."""
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    readers: list[Any] = [
        PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=endpoint))
    ]

    if console_export:
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))

    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)
    logger.info("[OTEL] MeterProvider installed (endpoint=%s)", endpoint)
    return provider


def _setup_propagators() -> None:
    """Set the global propagator to W3C TraceContext + Baggage."""
    try:
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.propagators.composite import CompositePropagator
        from opentelemetry.trace.propagation.tracecontext import (
            TraceContextTextMapPropagator,
        )

        set_global_textmap(
            CompositePropagator(
                [TraceContextTextMapPropagator(), W3CBaggagePropagator()]
            )
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("[OTEL] Propagator setup skipped: %s", exc)


def _instrument(enable_fastapi: bool, enable_redis: bool, enable_httpx: bool) -> None:
    """Best-effort auto-instrumentation for common libraries."""
    if enable_fastapi:
        _try_instrument(
            "opentelemetry.instrumentation.fastapi",
            "FastAPIInstrumentor",
            "FastAPI",
        )
    if enable_httpx:
        _try_instrument(
            "opentelemetry.instrumentation.httpx",
            "HTTPXClientInstrumentor",
            "HTTPX",
        )
    if enable_redis:
        _try_instrument(
            "opentelemetry.instrumentation.redis",
            "RedisInstrumentor",
            "Redis",
        )
    # Database instrumentation is opportunistic — only active when the
    # corresponding instrumentation extra is installed.
    _try_instrument(
        "opentelemetry.instrumentation.psycopg",
        "PsycopgInstrumentor",
        "psycopg",
        quiet=True,
    )


def _try_instrument(
    module_path: str, class_name: str, label: str, *, quiet: bool = False
) -> None:
    """Import and apply a single instrumentor, swallowing absence/errors."""
    try:
        import importlib

        instrumentor_cls = getattr(importlib.import_module(module_path), class_name)
        instrumentor_cls().instrument()
        logger.info("[OTEL] %s instrumentation enabled", label)
    except ImportError:
        log = logger.debug if quiet else logger.warning
        log("[OTEL] %s instrumentation not available", label)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[OTEL] %s instrumentation failed: %s", label, exc)


def setup_telemetry(
    service_name: str = "baselith-core",
    otlp_endpoint: str | None = None,
    *,
    enable_fastapi: bool = True,
    enable_redis: bool = True,
    enable_httpx: bool = True,
) -> bool:
    """
    Configure OpenTelemetry tracing and metrics for the application.

    Idempotent: the first successful call installs the providers; later calls
    are no-ops returning ``True``. Honors ``telemetry_enabled`` and the
    sampling/metrics/console flags from app config.

    Args:
        service_name: Logical service name for the OTel ``Resource``.
        otlp_endpoint: OTLP/gRPC collector endpoint. Falls back to
            ``telemetry_otel_endpoint`` from config.
        enable_fastapi: Auto-instrument FastAPI.
        enable_redis: Auto-instrument Redis.
        enable_httpx: Auto-instrument the HTTPX client.

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

        endpoint = otlp_endpoint or config.telemetry_otel_endpoint
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

            _setup_propagators()
            _instrument(enable_fastapi, enable_redis, enable_httpx)

            _initialized = True
            atexit.register(shutdown_telemetry)
            logger.info(
                "[OTEL] Telemetry initialized (service=%s, env=%s, sample_rate=%.2f)",
                service_name,
                getattr(config, "deployment_environment", "development"),
                sample_rate,
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
