"""Backward-compatible shim for the API Routers console module."""

import sys

import plugins.api_routers.console as _console
from plugins.api_routers.console import router

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _console

__all__ = ["router"]
