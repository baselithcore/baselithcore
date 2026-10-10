"""Types and models for the Coding Agent."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from baselith.core.guardrails.code_review import CodeReview


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
    #: The process exit code (124 on a sandbox timeout); None when the run never started.
    exit_code: int | None = None


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
    review: CodeReview | None = None
    #: The pytest file that verified final_code (empty when nothing ran).
    test_code: str = ""
    #: What changed, one short line each, as the model reported it.
    changes: list[str] = field(default_factory=list)
    #: How the verified loop ended: success, stalled, exhausted, budget_exceeded, failed, or
    #: "" when no loop ran (explain).
    loop_status: str = ""
    #: For a fix: whether the tests fail on the original code (they catch the bug). None when
    #: it was not checked.
    tests_reproduce_bug: bool | None = None
    #: Seconds the sandbox spent on this task.
    sandbox_seconds: float = 0.0
    #: The AI Act Art. 50 provenance tag of final_code (baselith.core.transparency), when marked.
    provenance: dict[str, Any] | None = None
