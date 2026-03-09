"""
Coding Agent Module.

Provides an autonomous agent for code generation, debugging, and testing.

Usage:
    from core.agents.coding import CodingAgent, CodingResult, CodeLanguage

    agent = CodingAgent()
    result = await agent.fix_code(buggy_code, error_message)
"""

from .agent import CodingAgent
from .types import (
    CodeExecutionResult,
    CodeLanguage,
    CodingResult,
    CodingTaskType,
)

__all__ = [
    "CodingAgent",
    "CodingResult",
    "CodeExecutionResult",
    "CodeLanguage",
    "CodingTaskType",
]
