"""Backward-compatible shim for the Coding Agent MCP tools module."""

import sys

from plugins.coding_agent.tools import register_coding_tools
import plugins.coding_agent.tools as _tools

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _tools

__all__ = ["register_coding_tools"]
