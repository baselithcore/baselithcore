"""Backward-compatible shim for the Coding Agent plugin package."""

from .agent import CodingAgent
from .types import (
    CodeExecutionResult,
    CodeLanguage,
    CodingResult,
    CodingTaskType,
)
from core.agents.coding_tools import register_coding_tools

__all__ = [
    "CodingAgent",
    "register_coding_tools",
    "CodingResult",
    "CodeExecutionResult",
    "CodeLanguage",
    "CodingTaskType",
]
