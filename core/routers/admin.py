"""Backward-compatible shim for the API Routers admin module."""

import sys

from core.utils.optional_import import optional_router, unavailable_admin_credentials

_admin, router = optional_router("plugins.api_routers.admin")
if _admin is not None:
    verify_credentials = getattr(_admin, "verify_credentials")
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _admin
else:
    verify_credentials = unavailable_admin_credentials

__all__ = ["router", "verify_credentials"]
