"""
Core observability package.

Provides tracing, audit logging, caching, metrics, and structured logging.
"""

from baselith.core.observability import metrics
from baselith.core.observability.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    audit_emit,
    get_audit_logger,
)
from baselith.core.observability.audit_chain import SQLiteAuditSink
from baselith.core.observability.cache import Cache, create_cache, get_cache
from baselith.core.observability.genai_semconv import provider_attributes
from baselith.core.observability.logging import (
    bind_context,
    configure_logging,
    get_logger,
)
from baselith.core.observability.openinference import (
    MAX_CONTENT_CHARS,
    openinference_enabled,
    openinference_llm_attributes,
)
from baselith.core.observability.otel import is_initialized, shutdown_telemetry
from baselith.core.observability.span_sink import (
    SpanRecord,
    emit_span,
    has_span_sinks,
    register_span_sink,
    unregister_span_sink,
)
from baselith.core.observability.telemetry import telemetry
from baselith.core.observability.tracing import (
    OTLPExporter,
    Span,
    SpanStatus,
    Tracer,
    get_tracer,
    setup_telemetry,
)

__all__ = [
    # Telemetry
    "telemetry",
    # Audit
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    "SQLiteAuditSink",
    "audit_emit",
    "get_audit_logger",
    # Cache
    "Cache",
    "create_cache",
    "get_cache",
    # Tracing
    "Tracer",
    "Span",
    "SpanStatus",
    "OTLPExporter",
    "setup_telemetry",
    "shutdown_telemetry",
    "is_initialized",
    "get_tracer",
    # OpenInference enrichment (LLM-observability backends)
    "MAX_CONTENT_CHARS",
    "openinference_enabled",
    "provider_attributes",
    "openinference_llm_attributes",
    # Span observation seam (in-process fan-out of completed spans)
    "SpanRecord",
    "emit_span",
    "has_span_sinks",
    "register_span_sink",
    "unregister_span_sink",
    # Logging
    "get_logger",
    "configure_logging",
    "bind_context",
    # Metrics
    "metrics",
]
