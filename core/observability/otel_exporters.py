"""OTLP exporter construction: protocol selection and endpoint shaping.

Split out of :mod:`core.observability.otel` so that module keeps owning
*provider lifecycle* while this one owns *how a signal leaves the process*.

Two things live here that the bootstrap used to hard-code:

* **Protocol.** The exporters were imported from
  ``opentelemetry.exporter.otlp.proto.grpc`` unconditionally, so a collector
  reachable only over HTTP/protobuf — the default for the OTel Collector's
  ``otlphttp`` receiver, for every vendor ingest endpoint that speaks plain
  HTTPS, and the only option behind an L7 proxy that will not forward HTTP/2
  trailers — could not be targeted at all. ``OTEL_EXPORTER_OTLP_PROTOCOL`` is
  the specification's own knob for this and is now honoured.

* **Endpoint shape.** The two protocols do not take the same URL. gRPC takes
  the collector root (``http://otel:4317``) and derives nothing. HTTP takes a
  **per-signal path** (``http://otel:4318/v1/traces``) and, when handed an
  explicit ``endpoint=`` argument, the SDK uses it verbatim — it only appends
  ``/v1/<signal>`` when the endpoint comes from the environment. Passing the
  same root URL to both is therefore a silent 404 loop on the HTTP side, which
  is exactly the failure this module exists to prevent: :func:`signal_endpoint`
  appends the path when it is missing.

Every exporter import is local to its factory. A deployment that installs
``opentelemetry-exporter-otlp-proto-grpc`` but not ``-http`` (or the reverse)
must fail only if it actually asks for the missing protocol.
"""

from __future__ import annotations

from typing import Any, Final

from core.observability.logging import get_logger

logger = get_logger(__name__)

#: Wire protocols this bootstrap can build an exporter for. ``http/json`` is a
#: valid value in the OTel specification but the Python SDK ships no exporter
#: for it, so it is treated as unknown rather than accepted and then ignored.
PROTOCOL_GRPC: Final = "grpc"
PROTOCOL_HTTP: Final = "http/protobuf"
_SUPPORTED_PROTOCOLS: Final = frozenset({PROTOCOL_GRPC, PROTOCOL_HTTP})

#: Aliases an operator plausibly writes. ``http`` and ``http/proto`` are not in
#: the specification, but they are what people type, and rejecting them means
#: falling back to gRPC against an HTTP-only collector — a silent black hole.
_PROTOCOL_ALIASES: Final = {
    "http": PROTOCOL_HTTP,
    "http/proto": PROTOCOL_HTTP,
    "httpprotobuf": PROTOCOL_HTTP,
    "otlp-http": PROTOCOL_HTTP,
    "otlp-grpc": PROTOCOL_GRPC,
}

#: Per-signal URL path the OTLP/HTTP receiver listens on.
_SIGNAL_PATHS: Final = {
    "traces": "/v1/traces",
    "metrics": "/v1/metrics",
    "logs": "/v1/logs",
}


def normalize_protocol(value: str | None) -> str:
    """Return a supported protocol name, defaulting to gRPC.

    Unknown values warn and fall back rather than raising: telemetry transport
    is never worth failing a boot over, and the warning names the value so the
    typo is findable.
    """
    raw = (value or "").strip().lower()
    if not raw:
        return PROTOCOL_GRPC
    resolved = _PROTOCOL_ALIASES.get(raw, raw)
    if resolved in _SUPPORTED_PROTOCOLS:
        return resolved
    logger.warning(
        "[OTEL] Unsupported OTLP protocol %r; using %s. Supported: %s",
        value,
        PROTOCOL_GRPC,
        ", ".join(sorted(_SUPPORTED_PROTOCOLS)),
    )
    return PROTOCOL_GRPC


def signal_endpoint(endpoint: str, protocol: str, signal: str) -> str:
    """Return the endpoint an exporter for ``signal`` should be handed.

    gRPC gets the collector root unchanged. HTTP gets ``/v1/<signal>``
    appended unless the caller already spelled a path out — an endpoint that
    ends in any of the known signal paths, or that carries a non-root path of
    its own (a collector behind ``/ingest/otlp``, say), is left alone.
    """
    if protocol != PROTOCOL_HTTP:
        return endpoint

    path = _SIGNAL_PATHS[signal]
    trimmed = endpoint.rstrip("/")
    if any(trimmed.endswith(known) for known in _SIGNAL_PATHS.values()):
        return trimmed
    return f"{trimmed}{path}"


def build_span_exporter(endpoint: str, protocol: str) -> Any:
    """Construct an OTLP span exporter for ``protocol``."""
    if protocol == PROTOCOL_HTTP:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPSpanExporter,
        )

        return HTTPSpanExporter(endpoint=signal_endpoint(endpoint, protocol, "traces"))

    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter as GRPCSpanExporter,
    )

    return GRPCSpanExporter(endpoint=endpoint)


def build_metric_exporter(endpoint: str, protocol: str) -> Any:
    """Construct an OTLP metric exporter for ``protocol``."""
    if protocol == PROTOCOL_HTTP:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HTTPMetricExporter,
        )

        return HTTPMetricExporter(
            endpoint=signal_endpoint(endpoint, protocol, "metrics")
        )

    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter as GRPCMetricExporter,
    )

    return GRPCMetricExporter(endpoint=endpoint)


def build_log_exporter(endpoint: str, protocol: str) -> Any:
    """Construct an OTLP log-record exporter for ``protocol``.

    The log exporters live under a private ``_log_exporter`` module in both
    protocol packages. That underscore is the SDK's, not a signal that the
    class is unstable: ``BatchLogRecordProcessor`` and ``LoggerProvider`` are
    exported the same way, and the OTLP logs *protocol* has been stable since
    v1.0. It is imported here in one place so the day it is promoted there is
    one line to change.
    """
    if protocol == PROTOCOL_HTTP:
        from opentelemetry.exporter.otlp.proto.http._log_exporter import (
            OTLPLogExporter as HTTPLogExporter,
        )

        return HTTPLogExporter(endpoint=signal_endpoint(endpoint, protocol, "logs"))

    from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (
        OTLPLogExporter as GRPCLogExporter,
    )

    return GRPCLogExporter(endpoint=endpoint)


__all__ = [
    "PROTOCOL_GRPC",
    "PROTOCOL_HTTP",
    "build_log_exporter",
    "build_metric_exporter",
    "build_span_exporter",
    "normalize_protocol",
    "signal_endpoint",
]
