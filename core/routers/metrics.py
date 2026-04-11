"""Backward-compatible shim for the API Routers metrics module."""

import sys

from plugins.api_routers.metrics import router
import plugins.api_routers.metrics as _metrics

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _metrics

__all__ = ["router"]
