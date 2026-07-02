"""Backward-compatible shim for the API Routers admin module."""

import sys

import plugins.api_routers.admin as _admin
from plugins.api_routers.admin import router, verify_credentials

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _admin

__all__ = ["router", "verify_credentials"]
