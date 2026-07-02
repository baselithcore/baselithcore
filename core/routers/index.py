"""Backward-compatible shim for the API Routers index module."""

import sys

import plugins.api_routers.index as _index
from plugins.api_routers.index import router

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _index

__all__ = ["router"]
