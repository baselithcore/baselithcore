"""Backward-compatible shim for the Coding Agent class module."""

import sys

from plugins.coding_agent.agent import CodingAgent
import plugins.coding_agent.agent as _agent

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _agent

__all__ = [
    "CodingAgent",
]
