"""Backward-compatible shim for the API Routers status module."""

import sys

from plugins.api_routers.status import router
import plugins.api_routers.status as _status

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _status

__all__ = ["router"]
