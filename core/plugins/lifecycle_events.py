"""Best-effort event-bus notifications for runtime plugin lifecycle changes.

Consumers (e.g. a control-plane dashboard) subscribe to these topics to learn
about enable/disable/reload outcomes without polling the plugin registry.
Emission is fire-and-forget: it must never affect the outcome of the
lifecycle operation that triggered it.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

logger = get_logger(__name__)

PLUGIN_ACTIVATED = "plugin.activated"
PLUGIN_DEACTIVATED = "plugin.deactivated"
PLUGIN_RELOADED = "plugin.reloaded"
PLUGIN_FAILED = "plugin.failed"

__all__ = [
    "PLUGIN_ACTIVATED",
    "PLUGIN_DEACTIVATED",
    "PLUGIN_RELOADED",
    "PLUGIN_FAILED",
    "emit_lifecycle_event",
]

# A failed disable leaves plugin state unchanged, so it has nothing to
# announce - emitting plugin.failed there would wrongly mark a still-healthy,
# still-active plugin as unhealthy for every subscriber.
_TOPIC_FOR: dict[tuple[str, bool], tuple[str, str]] = {
    ("enable", True): (PLUGIN_ACTIVATED, "active"),
    ("enable", False): (PLUGIN_FAILED, "failed"),
    ("disable", True): (PLUGIN_DEACTIVATED, "disabled"),
    ("reload", True): (PLUGIN_RELOADED, "active"),
    ("reload", False): (PLUGIN_FAILED, "failed"),
}


async def emit_lifecycle_event(
    op: str, plugin_name: str, ok: bool, **extra: Any
) -> None:
    """Publish the lifecycle topic for a completed runtime op.

    Never raises - a telemetry failure must not break plugin lifecycle
    management.
    """
    entry = _TOPIC_FOR.get((op, ok))
    if entry is None:
        return
    topic, state = entry
    try:
        from core.events.bus import get_event_bus

        await get_event_bus().emit(
            topic,
            {"plugin": plugin_name, "state": state, "op": op, "ok": ok, **extra},
            source="core.plugins.hotreload",
            wait=False,
        )
    except Exception as exc:
        logger.debug(f"lifecycle event emit failed ({op} {plugin_name}): {exc}")
