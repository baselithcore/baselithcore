"""
Core observability package.

Provides tracing, audit logging, caching, metrics, and structured logging.
"""

from core.observability import metrics
from core.observability.audit import AuditLogger, get_audit_logger
from core.observability.cache import Cache, create_cache, get_cache
from core.observability.logging import bind_context, configure_logging, get_logger
from core.observability.otel import is_initialized, shutdown_telemetry
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
    "AuditLogger",
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
    # Logging
    "get_logger",
    "configure_logging",
    "bind_context",
    # Metrics
    "metrics",
]
