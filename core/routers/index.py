"""Backward-compatible shim for the API Routers index module."""

import sys

from core.utils.optional_import import optional_router

_index, router = optional_router("plugins.api_routers.index")
if _index is not None:
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _index

__all__ = ["router"]
