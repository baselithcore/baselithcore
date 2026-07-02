"""Backward-compatible shim for the API Routers feedback module."""

import sys

import plugins.api_routers.feedback as _feedback
from plugins.api_routers.feedback import router

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _feedback

__all__ = ["router"]
