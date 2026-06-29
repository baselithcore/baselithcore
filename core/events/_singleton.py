"""Global :class:`~core.events.bus.EventBus` accessor.

Extracted from ``bus.py`` to keep that module under the 500-LOC cap. The
public import path is unchanged: ``from core.events.bus import get_event_bus``
still works (``bus`` re-exports these). ``EventBus`` is imported lazily inside
:func:`get_event_bus` to avoid an import cycle with ``bus``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from core.config import get_events_config
from core.events.validation import get_dead_letter_queue, get_schema_registry

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.events.bus import EventBus

# Global instance
_global_event_bus: Optional["EventBus"] = None


def get_event_bus() -> "EventBus":
    """Get the global event bus instance.

    Returns:
        Global EventBus instance.
    """
    global _global_event_bus
    if _global_event_bus is None:
        from core.events.bus import EventBus

        config = get_events_config()

        # Get global dependencies if enabled
        registry = get_schema_registry() if config.event_enable_validation else None
        dlq = get_dead_letter_queue() if config.event_enable_dlq else None

        _global_event_bus = EventBus(
            max_history=config.event_max_history,
            enable_wildcards=config.event_enable_wildcards,
            enable_validation=config.event_enable_validation,
            enable_dlq=config.event_enable_dlq,
            dlq_max_size=config.event_dlq_max_size,
            handler_timeout=config.event_handler_timeout,
            schema_registry=registry,
            dlq=dlq,
        )
    return _global_event_bus


def reset_event_bus() -> None:
    """Reset the global event bus (for testing)."""
    global _global_event_bus
    if _global_event_bus:
        _global_event_bus.clear_handlers()
    _global_event_bus = None
