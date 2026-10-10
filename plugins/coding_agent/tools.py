"""MCP tools exposed by the Coding Agent plugin.

Each verified tool returns the code under its historical key (``fixed_code``, ``generated_code``,
``test_code``, ``refactored_code``) plus what proves it: the tests that ran, how many passed,
how the loop ended and what changed.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .agent import CodingAgent
from .types import CodingResult

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)

_LANGUAGE: dict[str, Any] = {
    "type": "string",
    "description": "Language of the code; only python runs, any language can be explained",
    "enum": ["python", "javascript", "typescript"],
    "default": "python",
}


def _review_payload(result: CodingResult) -> dict[str, Any]:
    """Ride-along ``review`` entry for flagged results (empty when clean)."""
    if result.review is None or result.review.verdict != "flagged":
        return {}
    return {"review": asdict(result.review)}


def _payload(result: CodingResult, code_key: str) -> dict[str, Any]:
    """The tool answer for a verified task."""
    out: dict[str, Any] = {
        "status": "success" if result.success else "failed",
        code_key: result.final_code,
        "explanation": result.explanation,
        "iterations": result.iterations,
        "loop_status": result.loop_status,
        "tests_passed": result.tests_passed,
        "tests_total": result.tests_total,
        "changes": result.changes,
        "error": result.error,
        **_review_payload(result),
    }
    if code_key != "test_code":
        out["test_code"] = result.test_code
    if result.tests_reproduce_bug is not None:
        out["tests_reproduce_bug"] = result.tests_reproduce_bug
    return out


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {**properties, "language": _LANGUAGE},
        "required": required,
    }


def build_coding_tool_definitions() -> list[dict[str, Any]]:
    """Return coding MCP tool definitions with lazy agent creation."""
    coding_agent: CodingAgent | None = None

    def get_coding_agent() -> CodingAgent:
        nonlocal coding_agent
        if coding_agent is None:
            coding_agent = CodingAgent()
        return coding_agent

    async def fix_code(
        code: str, error_message: str, context: str = "", language: str = "python"
    ) -> dict[str, Any]:
        try:
            result = await get_coding_agent().fix_code(
                code, error_message, context, language=language
            )
            return _payload(result, "fixed_code")
        except Exception as exc:
            logger.error(
                "coding_tool_error", tool="fix_code", error_type=type(exc).__name__
            )
            return {"status": "error", "error": str(exc)}

    async def generate_code(
        description: str, examples: list[str] | None = None, language: str = "python"
    ) -> dict[str, Any]:
        try:
            result = await get_coding_agent().generate_code(
                description, examples, language=language
            )
            return _payload(result, "generated_code")
        except Exception as exc:
            logger.error(
                "coding_tool_error", tool="generate_code", error_type=type(exc).__name__
            )
            return {"status": "error", "error": str(exc)}

    async def generate_tests(
        code: str, test_framework: str = "pytest", language: str = "python"
    ) -> dict[str, Any]:
        try:
            result = await get_coding_agent().generate_tests(
                code, test_framework, language=language
            )
            return _payload(result, "test_code")
        except Exception as exc:
            logger.error(
                "coding_tool_error",
                tool="generate_tests",
                error_type=type(exc).__name__,
            )
            return {"status": "error", "error": str(exc)}

    async def explain_code(code: str, language: str = "python") -> dict[str, Any]:
        try:
            explanation = await get_coding_agent().explain_code(code, language=language)
            return {"status": "success", "explanation": explanation}
        except Exception as exc:
            logger.error(
                "coding_tool_error", tool="explain_code", error_type=type(exc).__name__
            )
            return {"status": "error", "error": str(exc)}

    async def refactor_code(
        code: str, goals: str = "", language: str = "python"
    ) -> dict[str, Any]:
        try:
            result = await get_coding_agent().refactor_code(
                code, goals, language=language
            )
            return _payload(result, "refactored_code")
        except Exception as exc:
            logger.error(
                "coding_tool_error", tool="refactor_code", error_type=type(exc).__name__
            )
            return {"status": "error", "error": str(exc)}

    text = {"type": "string"}
    return [
        {
            "name": "fix_code",
            "description": (
                "Fix buggy code. Writes tests that reproduce the bug, then retries the fix in "
                "an isolated sandbox until every test passes; returns the code and the tests."
            ),
            "input_schema": _schema(
                {
                    "code": {**text, "description": "The buggy code to fix"},
                    "error_message": {**text, "description": "The error message"},
                    "context": {
                        **text,
                        "description": "What the code should do",
                        "default": "",
                    },
                },
                ["code", "error_message"],
            ),
            "handler": fix_code,
            # Runs model-written code in the sandbox on every attempt.
            "category": "destructive",
        },
        {
            "name": "generate_code",
            "description": (
                "Write code from a description, with pytest tests it must pass in an isolated "
                "sandbox."
            ),
            "input_schema": _schema(
                {
                    "description": {**text, "description": "What the code should do"},
                    "examples": {
                        "type": "array",
                        "items": text,
                        "description": "Optional input/output examples",
                        "default": [],
                    },
                },
                ["description"],
            ),
            "handler": generate_code,
            "category": "destructive",
        },
        {
            "name": "generate_tests",
            "description": (
                "Write pytest tests for the given code and run them once against it; failing "
                "tests may point at bugs."
            ),
            "input_schema": _schema(
                {
                    "code": {**text, "description": "The code to generate tests for"},
                    "test_framework": {
                        **text,
                        "description": "Test style (both run under pytest)",
                        "enum": ["pytest", "unittest"],
                        "default": "pytest",
                    },
                },
                ["code"],
            ),
            "handler": generate_tests,
            # Since 1.1 the tests are executed in the sandbox.
            "category": "destructive",
        },
        {
            "name": "explain_code",
            "description": "Explain what the given code does, in any language.",
            "input_schema": _schema(
                {"code": {**text, "description": "The code to explain"}}, ["code"]
            ),
            "handler": explain_code,
            # Model-only explanation, nothing is executed.
            "category": "read_only",
        },
        {
            "name": "refactor_code",
            "description": (
                "Refactor code for the given goals. Tests pinning today's behaviour are written "
                "first and must stay green in the sandbox."
            ),
            "input_schema": _schema(
                {
                    "code": {**text, "description": "The code to refactor"},
                    "goals": {
                        **text,
                        "description": "Specific refactoring goals",
                        "default": "",
                    },
                },
                ["code"],
            ),
            "handler": refactor_code,
            "category": "destructive",
        },
    ]


def register_coding_tools(server: MCPServer) -> None:
    """Register coding tools with an MCP server."""
    definitions = build_coding_tool_definitions()
    for tool_def in definitions:
        server.register_tool(
            name=tool_def["name"],
            description=tool_def["description"],
            input_schema=tool_def["input_schema"],
            handler=tool_def["handler"],
            category=tool_def["category"],
        )
    logger.info("coding_tools_registered", tool_count=len(definitions))
