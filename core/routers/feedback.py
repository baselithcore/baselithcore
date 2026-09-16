"""Backward-compatible shim for the API Routers feedback module."""

import sys

from core.utils.optional_import import optional_router

_feedback, router = optional_router("plugins.api_routers.feedback")
if _feedback is not None:
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _feedback

__all__ = ["router"]
