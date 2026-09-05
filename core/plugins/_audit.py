"""Audit-trail helpers for plugin lifecycle events.

The loader and the registry record when a plugin is loaded or removed, so an
operator can answer "what code was running at time T" from the audit trail
rather than from log scrapes. Auditing observes the lifecycle; it must never
change it, so every failure here is swallowed and logged at debug level.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)


def _emit(
    event_name: str, plugin_name: str, action: str, details: dict[str, Any]
) -> None:
    try:
        from core.observability.audit import AuditEventType, audit_emit

        audit_emit(
            AuditEventType(event_name),
            resource=f"plugin:{plugin_name}",
            action=action,
            details=details,
        )
    except Exception:  # pragma: no cover - auditing never breaks the caller
        logger.debug("Audit sink unavailable for plugin event", exc_info=True)


def audit_plugin_load(
    plugin_name: str, *, version: str | None = None, path: str | None = None
) -> None:
    """Record that ``plugin_name`` finished ``initialize()``."""
    details: dict[str, Any] = {}
    if version is not None:
        details["version"] = version
    if path is not None:
        details["path"] = path
    _emit("plugin.load", plugin_name, "load", details)


def audit_plugin_unload(plugin_name: str) -> None:
    """Record that ``plugin_name`` was unregistered and shut down."""
    _emit("plugin.unload", plugin_name, "unload", {})


__all__ = ["audit_plugin_load", "audit_plugin_unload"]
