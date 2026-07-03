"""Backward-compatible shim for the Coding Agent plugin package."""

from core.agents.coding_tools import register_coding_tools

from .agent import CodingAgent
from .types import (
    CodeExecutionResult,
    CodeLanguage,
    CodingResult,
    CodingTaskType,
)

__all__ = [
    "CodeExecutionResult",
    "CodeLanguage",
    "CodingAgent",
    "CodingResult",
    "CodingTaskType",
    "register_coding_tools",
]
