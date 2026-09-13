"""Backward-compatible shim for the API Routers chat module."""

import sys

from core.utils.optional_import import optional_router

_chat, router = optional_router("plugins.api_routers.chat")
if _chat is not None:
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _chat

__all__ = ["router"]
