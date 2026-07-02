"""Backward-compatible shim for the Coding Agent types module."""

import sys

import plugins.coding_agent.types as _types
from plugins.coding_agent.types import (
    CodeExecutionResult,
    CodeLanguage,
    CodingResult,
    CodingTaskType,
)

# Register self as the plugin module for runtime compatibility
sys.modules[__name__] = _types

__all__ = [
    "CodeExecutionResult",
    "CodeLanguage",
    "CodingResult",
    "CodingTaskType",
]
