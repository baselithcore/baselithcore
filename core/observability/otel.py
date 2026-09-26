"""
Centralized OpenTelemetry SDK bootstrap (traces + metrics + logs).

This module is the **single source of truth** for OpenTelemetry provider
configuration. It builds a rich OTel ``Resource``, installs sampled
``TracerProvider``/``MeterProvider``/``LoggerProvider`` instances wired to an
OTLP collector, turns on auto-instrumentation for FastAPI/HTTPX/Redis/psycopg,
and sets the W3C propagators. All three signals share one ``Resource``, so a
backend can join a log line to the span it was written inside. The homegrown ``Tracer`` in
:mod:`core.observability.tracing` bridges into the ``TracerProvider``
configured here, so custom spans reach the collector alongside
auto-instrumentation spans.

Design rules:
- **Idempotent.** ``setup_telemetry`` may be called multiple times; only the
  first call installs providers. ``shutdown_telemetry`` flushes and tears them
  down (registered with ``atexit`` once, as a safety net).
- **Re-setup after shutdown works for traces.** OpenTelemetry lets the global
  ``TracerProvider`` be set only once per process, so the provider built by the
  first setup is kept for the life of the process and later setups swap new
  exporters in behind it (:mod:`core.observability.otel_swap`); its resource
  and sampler are the first setup's. OTLP metric push cannot be re-armed the
  same way — a second setup logs a warning and leaves metrics off.
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
- **Protocol is a setting, not a constant.** The wire protocol comes from
  ``TELEMETRY_OTEL_PROTOCOL`` / ``OTEL_EXPORTER_OTLP_PROTOCOL`` and exporter
  construction lives in :mod:`core.observability.otel_exporters`, which also
  shapes the per-signal endpoint HTTP needs and gRPC does not.
- **No reverse dependency.** This module imports only ``config``, ``logging``
  and its own ``otel_*`` siblings; ``tracing.py`` imports *from* here (lazily),
  never the reverse.

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
from core.observability.otel_exporters import (
    build_metric_exporter,
    build_span_exporter,
    normalize_protocol,
)
from core.observability.otel_instrumentation import (
    instrument_libraries,
    setup_propagators,
)
from core.observability.otel_logs import setup_log_export, shutdown_log_export
from core.observability.otel_swap import build_swappable_processor

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
# Process-lifetime state: OTel refuses to replace a global provider, so these
# survive shutdown_telemetry() and are re-used by the next setup.
_global_tracer_provider: Any = None
_span_pipeline: Any = None
_meter_provider_was_installed = False
_atexit_registered = False


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


#: ``OTEL_TRACES_SAMPLER`` values this bootstrap understands. Matches the
#: OpenTelemetry environment-variable specification; the SDK's own
#: ``jaeger_remote`` and ``xray`` samplers need extra packages and are treated
#: as unknown (warn + fall back) rather than pretended to support.
_ENV_SAMPLER = "OTEL_TRACES_SAMPLER"
_ENV_SAMPLER_ARG = "OTEL_TRACES_SAMPLER_ARG"
_KNOWN_SAMPLERS = frozenset(
    {
        "always_on",
        "always_off",
        "traceidratio",
        "parentbased_always_on",
        "parentbased_always_off",
        "parentbased_traceidratio",
    }
)


def _sampler_arg_ratio() -> float:
    """``OTEL_TRACES_SAMPLER_ARG`` as a ratio clamped to [0, 1].

    An absent or unparsable value means 1.0 — the SDK's own default. Sampling
    *less* than asked because a typo slipped into a chart value is the failure
    mode that silently empties a trace backend, so it is logged loudly.
    """
    raw = (os.getenv(_ENV_SAMPLER_ARG) or "").strip()
    if not raw:
        return 1.0
    try:
        return max(0.0, min(1.0, float(raw)))
    except ValueError:
        logger.warning(
            "[OTEL] %s=%r is not a number; using ratio 1.0", _ENV_SAMPLER_ARG, raw
        )
        return 1.0


def _sampler_from_env() -> Any | None:
    """Build the sampler named by ``OTEL_TRACES_SAMPLER``, or ``None``.

    ``None`` means "nothing configured (or nothing we understand)" and the
    caller falls back to the ``telemetry_traces_sample_rate`` setting.
    """
    name = (os.getenv(_ENV_SAMPLER) or "").strip().lower()
    if not name:
        return None
    if name not in _KNOWN_SAMPLERS:
        logger.warning(
            "[OTEL] %s=%r is not supported; falling back to "
            "telemetry_traces_sample_rate",
            _ENV_SAMPLER,
            name,
        )
        return None

    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )

    if name == "always_on":
        return ALWAYS_ON
    if name == "always_off":
        return ALWAYS_OFF
    if name == "traceidratio":
        return TraceIdRatioBased(_sampler_arg_ratio())
    if name == "parentbased_always_on":
        return ParentBased(root=ALWAYS_ON)
    if name == "parentbased_always_off":
        return ParentBased(root=ALWAYS_OFF)
    return ParentBased(root=TraceIdRatioBased(_sampler_arg_ratio()))


def _build_sampler(sample_rate: float) -> Any:
    """Return the trace sampler to install.

    ``OTEL_TRACES_SAMPLER``/``OTEL_TRACES_SAMPLER_ARG`` win when set: the SDK
    honours them only for a ``TracerProvider`` built without an explicit
    sampler, and this bootstrap always passes one — so an operator who set the
    standard variables (and every chart and sidecar that sets them for you) was
    silently ignored. With neither set, the historical behaviour is unchanged:
    a ParentBased(TraceIdRatio) sampler at ``sample_rate``, clamped to [0, 1].
    """
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )

    from_env = _sampler_from_env()
    if from_env is not None:
        logger.info("[OTEL] Sampler from %s=%s", _ENV_SAMPLER, os.getenv(_ENV_SAMPLER))
        return from_env

    rate = max(0.0, min(1.0, sample_rate))
    if rate >= 1.0:
        return ParentBased(root=ALWAYS_ON)
    return ParentBased(root=TraceIdRatioBased(rate))


def _setup_tracing(
    resource: Any,
    endpoint: str | None,
    protocol: str,
    sampler: Any,
    console_export: bool,
) -> Any:
    """Install a TracerProvider with optional OTLP and console export.

    ``endpoint`` of ``None`` installs the provider with no OTLP exporter: spans
    are sampled, instrumented and handed to in-process sinks, but nothing is
    shipped off-box.
    """
    global _global_tracer_provider, _span_pipeline

    processors: list[Any] = []
    if endpoint is not None:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        processors.append(BatchSpanProcessor(build_span_exporter(endpoint, protocol)))

    if console_export:
        from opentelemetry.sdk.trace.export import (
            ConsoleSpanExporter,
            SimpleSpanProcessor,
        )

        processors.append(SimpleSpanProcessor(ConsoleSpanExporter()))

    if _global_tracer_provider is None:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(resource=resource, sampler=sampler)
        pipeline = build_swappable_processor()
        provider.add_span_processor(pipeline)

        # In-process mirror: hand every finished span to locally registered
        # sinks (dashboards, debug readers) alongside the OTLP export. No-op
        # when nobody is listening; never fails setup.
        from core.observability.span_bridge import install_span_sink_bridge

        install_span_sink_bridge(provider)

        trace.set_tracer_provider(provider)
        _global_tracer_provider = provider
        _span_pipeline = pipeline
    else:
        logger.info(
            "[OTEL] Re-using the process TracerProvider "
            "(resource and sampler of the first setup are kept)"
        )

    _span_pipeline.replace(processors)
    logger.info(
        "[OTEL] TracerProvider installed (export=%s)",
        endpoint if endpoint is not None else "in-process only",
    )
    return _global_tracer_provider


def _setup_metrics(
    resource: Any, endpoint: str | None, protocol: str, console_export: bool
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
        readers.append(
            PeriodicExportingMetricReader(build_metric_exporter(endpoint, protocol))
        )

    if console_export:
        from opentelemetry.sdk.metrics.export import ConsoleMetricExporter

        readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))

    if not readers:
        logger.info("[OTEL] MeterProvider skipped (no OTLP endpoint, no console)")
        return None

    global _meter_provider_was_installed
    if _meter_provider_was_installed:
        # The global MeterProvider can be set once per process and its readers
        # are fixed at construction, so a shut-down one cannot be re-armed.
        for reader in readers:
            reader.shutdown()
        logger.warning(
            "[OTEL] OTLP metric export cannot be re-enabled after "
            "shutdown_telemetry() in the same process; metrics stay off"
        )
        return None

    provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(provider)
    _meter_provider_was_installed = True
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
        otlp_endpoint: OTLP collector endpoint, in the shape the configured
            protocol expects. Falls back to ``telemetry_otel_endpoint``. Empty (or blank) means no
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
    global _initialized, _tracer_provider, _meter_provider, _atexit_registered

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
        protocol = normalize_protocol(getattr(config, "telemetry_otel_protocol", None))

        try:
            resource = _build_resource(service_name, config)
            sampler = _build_sampler(sample_rate)
            _tracer_provider = _setup_tracing(
                resource, endpoint, protocol, sampler, console_export
            )

            if getattr(config, "telemetry_metrics_enabled", False):
                _meter_provider = _setup_metrics(
                    resource, endpoint, protocol, console_export
                )

            if getattr(config, "telemetry_logs_enabled", False):
                setup_log_export(
                    resource,
                    endpoint,
                    protocol,
                    console_export=console_export,
                )

            setup_propagators()
            instrument_libraries(enable_fastapi, enable_redis, enable_httpx, app)

            _initialized = True
            if not _atexit_registered:
                atexit.register(shutdown_telemetry)
                _atexit_registered = True
            logger.info(
                "[OTEL] Telemetry initialized "
                "(service=%s, env=%s, sample_rate=%.2f, protocol=%s, export=%s)",
                service_name,
                getattr(config, "deployment_environment", "development"),
                sample_rate,
                protocol,
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

        # Logs first: the handler is attached to the root logger, so detaching
        # it before the other providers tear down keeps their own shutdown
        # chatter out of a processor that is already flushing.
        shutdown_log_export()

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
