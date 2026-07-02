"""Backward-compatible shim for the API Routers tenant module."""

import sys

import plugins.api_routers.tenant as _tenant
from plugins.api_routers.tenant import router

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _tenant

__all__ = ["router"]
