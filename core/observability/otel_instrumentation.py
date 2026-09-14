"""Auto-instrumentation and propagator wiring for the OTel backbone.

Split out of :mod:`core.observability.otel` so each file stays focused (and
under the repository's file-size cap): ``otel`` owns *provider* configuration —
resource, sampler, exporters, lifecycle — while this module owns *what gets
instrumented and how*. ``otel`` imports these helpers; nothing here imports
back.

Every import of an OpenTelemetry instrumentation package is guarded: a missing
package downgrades to a log line and the framework keeps running.
"""

from __future__ import annotations

import os
from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "instrument_libraries",
    "setup_propagators",
]


def setup_propagators() -> None:
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


def instrument_libraries(
    enable_fastapi: bool,
    enable_redis: bool,
    enable_httpx: bool,
    app: Any = None,
) -> None:
    """Best-effort auto-instrumentation for common libraries."""
    if enable_fastapi:
        _try_instrument(
            "opentelemetry.instrumentation.fastapi",
            "FastAPIInstrumentor",
            "FastAPI",
            excluded_urls=_fastapi_excluded_urls(),
            exclude_spans=_fastapi_exclude_spans(),
        )
        _instrument_app(app)
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


#: Liveness/readiness probes and the Prometheus scrape hit every pod every
#: 10-30s and carry no user work: tracing them is pure exporter/collector
#: cost. Anchored regexes (the instrumentation ``re.search``es the full URL) so
#: a route that merely *contains* "health" is still traced.
_DEFAULT_FASTAPI_EXCLUDED_URLS = "/health$,/health/ready$,/metrics$"


def _fastapi_excluded_urls() -> str | None:
    """Return the probe/scrape exclusion list unless the operator set one.

    The OTel SDK's own ``OTEL_PYTHON_FASTAPI_EXCLUDED_URLS`` /
    ``OTEL_PYTHON_EXCLUDED_URLS`` win when present: passing the kwarg would
    silently override them.
    """
    if os.getenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS") or os.getenv(
        "OTEL_PYTHON_EXCLUDED_URLS"
    ):
        return None
    return _DEFAULT_FASTAPI_EXCLUDED_URLS


def _fastapi_exclude_spans() -> list[str] | None:
    """Drop the ASGI ``receive``/``send`` sub-spans unless asked to keep them.

    The FastAPI instrumentation wraps each request in a server span *and* one
    span per ASGI message, so a plain request costs three spans instead of one.
    They carry no routing, timing or error information a reader wants — in a
    waterfall they are two duplicate rows under every request — while tripling
    exporter volume, retention-ring pressure and the cost of every in-process
    consumer. Off by default; ``BASELITH_OTEL_ASGI_SUB_SPANS=true`` restores
    them for someone debugging ASGI streaming itself.

    Returns ``None`` (kwarg omitted, instrumentation unchanged) when the
    installed instrumentation predates ``exclude_spans``: passing an unknown
    keyword would raise and disable FastAPI tracing altogether.
    """
    if os.getenv("BASELITH_OTEL_ASGI_SUB_SPANS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return None
    try:
        import inspect

        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        params = inspect.signature(FastAPIInstrumentor.instrument_app).parameters
        if "exclude_spans" not in params:
            return None
    except Exception:  # silent-ok: probing an optional package's signature; any failure means "assume unsupported", and the caller omits the kwarg
        return None
    return ["receive", "send"]


def _instrument_app(app: Any) -> None:
    """Instrument one already-constructed FastAPI application.

    ``FastAPIInstrumentor().instrument()`` works by replacing the
    ``fastapi.FastAPI`` class, so it only reaches apps constructed *after* the
    call. Telemetry is initialized from the lifespan, which by definition runs
    once the application object already exists — so the global patch covered
    every sub-app a plugin mounts later and missed the one serving the traffic.
    The symptom is the worst kind: instrumentation reports itself enabled, and
    not a single HTTP server span is ever produced.

    Instrumenting the instance directly closes that ordering hole. It is a
    no-op when *app* is ``None`` (a non-HTTP entrypoint: the CLI, a worker) or
    already instrumented.
    """
    if app is None:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        if getattr(app, "_is_instrumented_by_opentelemetry", False):
            return
        kwargs: dict[str, Any] = {}
        excluded = _fastapi_excluded_urls()
        if excluded is not None:
            kwargs["excluded_urls"] = excluded
        exclude_spans = _fastapi_exclude_spans()
        if exclude_spans is not None:
            kwargs["exclude_spans"] = exclude_spans
        FastAPIInstrumentor.instrument_app(app, **kwargs)
        logger.info("[OTEL] FastAPI app instrumented (%s)", type(app).__name__)
    except ImportError:
        logger.debug("[OTEL] FastAPI instrumentation not available for the app")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[OTEL] Could not instrument the running app: %s", exc)


def _try_instrument(
    module_path: str,
    class_name: str,
    label: str,
    *,
    quiet: bool = False,
    **instrument_kwargs: Any,
) -> None:
    """Import and apply a single instrumentor, swallowing absence/errors.

    ``instrument_kwargs`` are forwarded to ``instrument()``; ``None`` values
    are dropped so an instrumentor's own default/env resolution still applies.
    """
    try:
        import importlib

        instrumentor_cls = getattr(importlib.import_module(module_path), class_name)
        kwargs = {k: v for k, v in instrument_kwargs.items() if v is not None}
        instrumentor_cls().instrument(**kwargs)
        logger.info("[OTEL] %s instrumentation enabled", label)
    except ImportError:
        log = logger.debug if quiet else logger.warning
        log("[OTEL] %s instrumentation not available", label)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[OTEL] %s instrumentation failed: %s", label, exc)
