"""Back-compat shim re-exporting the dashboard API from the ``dashboard`` package.

The implementation was split out of this monolithic module to honor the
framework's 500-line file cap; see ``plugins/baselithbot/dashboard/`` for
the per-concern modules (bus, schemas, security, session driver, routes).

External imports continue to work unchanged::

    from plugins.baselithbot.ui_api import create_dashboard_router, get_event_bus
"""

from __future__ import annotations

from .dashboard import DashboardEventBus, create_dashboard_router, get_event_bus
from .dashboard.bus import _BUS
from .dashboard.schemas import (
    CronToggleRequest,
    PairingTokenRequest,
    ProviderKeyRequest,
    SessionCreateRequest,
    SessionSendRequest,
)

__all__ = [
    "CronToggleRequest",
    "DashboardEventBus",
    "PairingTokenRequest",
    "ProviderKeyRequest",
    "SessionCreateRequest",
    "SessionSendRequest",
    "_BUS",
    "create_dashboard_router",
    "get_event_bus",
]
