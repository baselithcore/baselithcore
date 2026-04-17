"""Baselithbot dashboard REST + SSE API package.

Split from the legacy ``ui_api`` module to respect the 500-line file cap.
Public entry points remain ``create_dashboard_router`` and ``get_event_bus``.
"""

from __future__ import annotations

from .app import create_dashboard_router
from .bus import DashboardEventBus, _BUS, get_event_bus

__all__ = [
    "DashboardEventBus",
    "_BUS",
    "create_dashboard_router",
    "get_event_bus",
]
