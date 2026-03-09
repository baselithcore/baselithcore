"""
Audit logging system.

Provides structured audit logging for security-relevant events.
"""

from __future__ import annotations

import asyncio
import json
import logging
from core.observability.logging import get_logger
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, List

logger = get_logger(__name__)


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

    # Generic
    CUSTOM = "custom"


class AuditEvent:
    """Represents a single audit event."""

    def __init__(
        self,
        event_type: AuditEventType,
        *,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        resource: Optional[str] = None,
        action: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        success: bool = True,
        ip_address: Optional[str] = None,
    ) -> None:
        self.timestamp = datetime.now(timezone.utc)
        self.event_type = event_type
        self.user_id = user_id
        self.session_id = session_id
        self.resource = resource
        self.action = action
        self.details = details or {}
        self.success = success
        self.ip_address = ip_address

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "user_id": self.user_id,
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

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
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

    def __init__(self, sinks: Optional[List[AuditSink]] = None) -> None:
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
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        resource: Optional[str] = None,
        action: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        success: bool = True,
        ip_address: Optional[str] = None,
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
        )

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
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
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
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
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
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
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
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    """Get or create the global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
        # Add default logger sink
        _audit_logger.add_sink(LoggerAuditSink())
    return _audit_logger


__all__ = [
    "AuditEventType",
    "AuditEvent",
    "AuditSink",
    "FileAuditSink",
    "LoggerAuditSink",
    "AuditLogger",
    "get_audit_logger",
]
