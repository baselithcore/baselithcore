"""Backward-compatible shim for the API Routers console module."""

import sys

from baselith.core.utils.optional_import import optional_router

_console, router = optional_router("baselith.plugins.api_routers.routes.console")
if _console is not None:
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _console

__all__ = ["router"]
