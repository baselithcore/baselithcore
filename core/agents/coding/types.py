"""
Types and models for the Coding Agent.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CodeLanguage(str, Enum):
    """Supported programming languages."""

    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class CodingTaskType(str, Enum):
    """Types of coding tasks."""

    FIX_BUG = "fix_bug"
    GENERATE_CODE = "generate_code"
    GENERATE_TESTS = "generate_tests"
    REFACTOR = "refactor"
    EXPLAIN = "explain"
    OPTIMIZE = "optimize"


@dataclass
class CodeExecutionResult:
    """Result of code execution in sandbox."""

    success: bool
    output: str = ""
    error: str = ""
    execution_time_ms: float = 0.0
    return_value: Any = None


@dataclass
class CodingResult:
    """Result of a coding task."""

    success: bool
    original_code: str
    final_code: str
    iterations: int = 0
    error: str | None = None
    explanation: str = ""
    tests_passed: int = 0
    tests_total: int = 0
    execution_results: list[CodeExecutionResult] = field(default_factory=list)
