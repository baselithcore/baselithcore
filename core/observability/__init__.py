"""
Core observability package.

Provides tracing, audit logging, caching, metrics, and structured logging.
"""

from core.observability.audit import AuditLogger, get_audit_logger
from core.observability.cache import Cache, create_cache, get_cache
from core.observability.tracing import (
    Tracer,
    Span,
    SpanStatus,
    OTLPExporter,
    setup_telemetry,
    get_tracer,
)
from core.observability.logging import get_logger, configure_logging, bind_context
from core.observability import metrics
from core.observability.telemetry import telemetry

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
    "get_tracer",
    # Logging
    "get_logger",
    "configure_logging",
    "bind_context",
    # Metrics
    "metrics",
]
