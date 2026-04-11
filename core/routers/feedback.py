"""Backward-compatible shim for the API Routers feedback module."""

import sys

from plugins.api_routers.feedback import router
import plugins.api_routers.feedback as _feedback

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _feedback

__all__ = ["router"]
