"""Backward-compatible shim for the API Routers metrics module."""

import sys

from core.utils.optional_import import optional_router

_metrics, router = optional_router("plugins.api_routers.metrics")
if _metrics is not None:
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _metrics

__all__ = ["router"]
