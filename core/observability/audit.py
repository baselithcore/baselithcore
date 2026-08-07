"""
Audit logging system.

Provides structured audit logging for security-relevant events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from core.observability.logging import get_logger

logger = get_logger(__name__)

# Fire-and-forget emissions from synchronous call sites are scheduled on the
# running loop; keeping a strong reference prevents the task from being
# garbage-collected mid-flight (see ``audit_emit``).
_pending_tasks: set[asyncio.Task[None]] = set()


class AuditEventType(str, Enum):
    """Types of audit events."""

    # Authentication
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_FAILED = "auth.failed"

    # Data access
    DATA_READ = "data.read"
    DATA_WRITE = "data.write"
    DATA_DELETE = "data.delete"

    # API operations
    API_REQUEST = "api.request"
    API_ERROR = "api.error"

    # Agent operations
    AGENT_INVOKE = "agent.invoke"
    AGENT_COMPLETE = "agent.complete"
    AGENT_ERROR = "agent.error"

    # Plugin operations
    PLUGIN_LOAD = "plugin.load"
    PLUGIN_UNLOAD = "plugin.unload"
    PLUGIN_ERROR = "plugin.error"

    # Chat operations
    CHAT_REQUEST = "chat.request"
    CHAT_RESPONSE = "chat.response"

    # Admin operations
    ADMIN_ACTION = "admin.action"
    CONFIG_CHANGE = "config.change"

    # Data-subject rights (GDPR Chapter III)
    PRIVACY_EXPORT = "privacy.export"
    PRIVACY_ERASE = "privacy.erase"
    PRIVACY_RECTIFY = "privacy.rectify"
    PRIVACY_RESTRICT = "privacy.restrict"
    PRIVACY_OBJECT = "privacy.object"
    PRIVACY_CONSENT = "privacy.consent"
    PRIVACY_RETENTION = "privacy.retention"

    # AI transparency (EU AI Act Art. 50)
    TRANSPARENCY_DISCLOSURE = "transparency.disclosure"
    TRANSPARENCY_MARK = "transparency.mark"

    # Regulatory incident reporting (NIS2 Art. 23, DORA Art. 19,
    # AI Act Art. 73, GDPR Art. 33/34)
    INCIDENT_OPEN = "incident.open"
    INCIDENT_MILESTONE = "incident.milestone"
    INCIDENT_CLOSE = "incident.close"

    # AI-system governance (EU AI Act Art. 11/17/27/72)
    COMPLIANCE_REGISTER = "compliance.register"
    COMPLIANCE_ASSESSMENT = "compliance.assessment"

    # Generic
    CUSTOM = "custom"


class AuditEvent:
    """Represents a single audit event."""

    def __init__(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        resource: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
        ip_address: str | None = None,
        tenant_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        self.timestamp = datetime.now(UTC)
        self.event_type = event_type
        self.user_id = user_id
        self.session_id = session_id
        self.resource = resource
        self.action = action
        self.details = details or {}
        self.success = success
        self.ip_address = ip_address
        self.tenant_id = tenant_id
        # Stable identity so the same event can be correlated across sinks and
        # deduplicated by an external SIEM.
        self.event_id = event_id or str(uuid4())

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "resource": self.resource,
            "action": self.action,
            "details": self.details,
            "success": self.success,
            "ip_address": self.ip_address,
        }

    def to_json(self) -> str:
        """Convert event to JSON string."""
        return json.dumps(self.to_dict(), default=str)


class AuditSink(Protocol):
    """Protocol for audit log sinks."""

    async def write(self, event: AuditEvent) -> None:
        """Write an audit event asynchronously."""
        ...


class FileAuditSink:
    """Writes audit events to a file (JSON lines format) using non-blocking executor."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def write(self, event: AuditEvent) -> None:
        """Append event to file in a thread pool."""
        loop = asyncio.get_running_loop()
        payload = event.to_json() + "\n"
        await loop.run_in_executor(None, self._append_to_file, payload)

    def _append_to_file(self, content: str) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(content)


class LoggerAuditSink:
    """Writes audit events to Python logger."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or get_logger("audit")

    async def write(self, event: AuditEvent) -> None:
        """Log event at INFO level."""
        self.logger.info(event.to_json())


class AuditLogger:
    """
    Main audit logger that writes to multiple sinks.

    Usage:
        audit = get_audit_logger()
        await audit.log(AuditEventType.AUTH_LOGIN, user_id="user123")
    """

    def __init__(self, sinks: list[AuditSink] | None = None) -> None:
        self.sinks = sinks or []
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """Check if audit logging is enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable audit logging."""
        self._enabled = value

    def add_sink(self, sink: AuditSink) -> None:
        """Add an audit sink."""
        self.sinks.append(sink)

    async def log(
        self,
        event_type: AuditEventType,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        resource: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
        success: bool = True,
        ip_address: str | None = None,
        tenant_id: str | None = None,
    ) -> None:
        """
        Log an audit event asynchronously.

        Args:
            event_type: Type of event
            user_id: User identifier
            session_id: Session identifier
            resource: Resource being accessed
            action: Specific action taken
            details: Additional event details
            success: Whether operation succeeded
            ip_address: Client IP address
            tenant_id: Owning tenant, when the deployment is multi-tenant
        """
        if not self._enabled:
            return

        event = AuditEvent(
            event_type,
            user_id=user_id,
            session_id=session_id,
            resource=resource,
            action=action,
            details=details,
            success=success,
            ip_address=ip_address,
            tenant_id=tenant_id,
        )
        await self.log_event(event)

    async def log_event(self, event: AuditEvent) -> None:
        """Fan an already-built :class:`AuditEvent` out to every sink.

        Sink failures are contained: one broken sink must never break the
        request path, nor stop the remaining sinks from recording the event.
        """
        if not self._enabled:
            return

        for sink in self.sinks:
            try:
                await sink.write(event)
            except Exception as e:
                # Don't let sink errors break the application
                # But do log them
                logger.error(f"[AUDIT] Sink write failed: {e}")

    async def log_auth(
        self,
        success: bool,
        user_id: str | None = None,
        ip_address: str | None = None,
        **details,
    ) -> None:
        """Log authentication event."""
        event_type = (
            AuditEventType.AUTH_LOGIN if success else AuditEventType.AUTH_FAILED
        )
        await self.log(
            event_type,
            user_id=user_id,
            ip_address=ip_address,
            success=success,
            details=details,
        )

    async def log_api_request(
        self,
        method: str,
        path: str,
        user_id: str | None = None,
        ip_address: str | None = None,
        **details,
    ) -> None:
        """Log API request."""
        await self.log(
            AuditEventType.API_REQUEST,
            user_id=user_id,
            resource=path,
            action=method,
            ip_address=ip_address,
            details=details,
        )

    async def log_chat(
        self,
        query: str,
        session_id: str | None = None,
        user_id: str | None = None,
        **details,
    ) -> None:
        """Log chat request."""
        await self.log(
            AuditEventType.CHAT_REQUEST,
            user_id=user_id,
            session_id=session_id,
            action="query",
            details={"query": query[:200], **details},  # Truncate for privacy
        )


# Global instance
_audit_logger: AuditLogger | None = None


def get_audit_logger() -> AuditLogger:
    """Get or create the global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
        # Add default logger sink
        _audit_logger.add_sink(LoggerAuditSink())
    return _audit_logger


def set_audit_logger(audit_logger: AuditLogger) -> None:
    """Install a pre-configured audit logger as the global instance."""
    global _audit_logger
    _audit_logger = audit_logger


def reset_audit_logger() -> None:
    """Drop the global audit logger (tests, and reconfiguration at startup)."""
    global _audit_logger
    _audit_logger = None


def audit_emit(
    event_type: AuditEventType,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    resource: str | None = None,
    action: str | None = None,
    details: dict[str, Any] | None = None,
    success: bool = True,
    ip_address: str | None = None,
    tenant_id: str | None = None,
) -> None:
    """Record an audit event from a **synchronous** call site.

    Audit recording must never change the control flow of the code it observes,
    so this helper is deliberately fire-and-forget:

    * with a running event loop the write is scheduled as a task (a strong
      reference is held until completion so it cannot be collected mid-flight);
    * without one — module import, a worker thread, a sync CLI path — the event
      is written synchronously to the logger sink only, which never blocks.

    Async call sites should ``await get_audit_logger().log(...)`` instead.
    """
    audit_logger = get_audit_logger()
    if not audit_logger.enabled:
        return

    event = AuditEvent(
        event_type,
        user_id=user_id,
        session_id=session_id,
        resource=resource,
        action=action,
        details=details,
        success=success,
        ip_address=ip_address,
        tenant_id=tenant_id,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop: degrade to the non-blocking logger representation rather
        # than dropping the event entirely.
        logger.info(event.to_json())
        return

    task = loop.create_task(audit_logger.log_event(event))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    "AuditSink",
    "FileAuditSink",
    "LoggerAuditSink",
    "audit_emit",
    "get_audit_logger",
    "reset_audit_logger",
    "set_audit_logger",
]
