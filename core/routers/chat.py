"""Backward-compatible shim for the API Routers chat module."""

import sys

import plugins.api_routers.chat as _chat
from plugins.api_routers.chat import router

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _chat

__all__ = ["router"]
