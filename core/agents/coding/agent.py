"""Backward-compatible shim for the Coding Agent class module."""

import sys

import plugins.coding_agent.agent as _agent
from plugins.coding_agent.agent import CodingAgent

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _agent

__all__ = [
    "CodingAgent",
]
