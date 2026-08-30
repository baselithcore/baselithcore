"""
Core observability package.

Provides tracing, audit logging, caching, metrics, and structured logging.
"""

from core.observability import metrics
from core.observability.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    audit_emit,
    get_audit_logger,
)
from core.observability.audit_chain import SQLiteAuditSink
from core.observability.cache import Cache, create_cache, get_cache
from core.observability.logging import bind_context, configure_logging, get_logger
from core.observability.openinference import (
    MAX_CONTENT_CHARS,
    openinference_enabled,
    openinference_llm_attributes,
)
from core.observability.otel import is_initialized, shutdown_telemetry
from core.observability.span_sink import (
    SpanRecord,
    emit_span,
    has_span_sinks,
    register_span_sink,
    unregister_span_sink,
)
from core.observability.telemetry import telemetry
from core.observability.tracing import (
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
