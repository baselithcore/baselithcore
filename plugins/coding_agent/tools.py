"""MCP tools exposed by the Coding Agent plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .agent import CodingAgent

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def build_coding_tool_definitions() -> list[dict[str, Any]]:
    """Return coding MCP tool definitions with lazy agent creation."""
    coding_agent: CodingAgent | None = None

    async def get_coding_agent() -> CodingAgent:
        nonlocal coding_agent
        if coding_agent is None:
            coding_agent = CodingAgent()
        return coding_agent

    async def fix_code(
        code: str, error_message: str, context: str = ""
    ) -> dict[str, Any]:
        try:
            agent = await get_coding_agent()
            result = await agent.fix_code(code, error_message, context)
            return {
                "status": "success" if result.success else "failed",
                "fixed_code": result.final_code,
                "iterations": result.iterations,
                "error": result.error,
                "explanation": result.explanation,
            }
        except Exception as exc:
            logger.error("coding_tool_error", tool="fix_code", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def generate_code(
        description: str, examples: list[str] | None = None
    ) -> dict[str, Any]:
        try:
            agent = await get_coding_agent()
            result = await agent.generate_code(description, examples)
            return {
                "status": "success" if result.success else "failed",
                "generated_code": result.final_code,
                "error": result.error,
            }
        except Exception as exc:
            logger.error("coding_tool_error", tool="generate_code", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def generate_tests(
        code: str, test_framework: str = "pytest"
    ) -> dict[str, Any]:
        try:
            agent = await get_coding_agent()
            result = await agent.generate_tests(code, test_framework)
            return {
                "status": "success" if result.success else "failed",
                "test_code": result.final_code,
                "error": result.error,
            }
        except Exception as exc:
            logger.error("coding_tool_error", tool="generate_tests", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def explain_code(code: str) -> dict[str, Any]:
        try:
            agent = await get_coding_agent()
            explanation = await agent.explain_code(code)
            return {"status": "success", "explanation": explanation}
        except Exception as exc:
            logger.error("coding_tool_error", tool="explain_code", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def refactor_code(code: str, goals: str = "") -> dict[str, Any]:
        try:
            agent = await get_coding_agent()
            result = await agent.refactor_code(code, goals)
            return {
                "status": "success" if result.success else "failed",
                "refactored_code": result.final_code,
                "explanation": result.explanation,
                "error": result.error,
            }
        except Exception as exc:
            logger.error("coding_tool_error", tool="refactor_code", error=str(exc))
            return {"status": "error", "error": str(exc)}

    return [
        {
            "name": "fix_code",
            "description": (
                "Fix buggy code using an auto-debug loop. "
                "Analyzes errors and iteratively fixes until success."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The buggy code to fix",
                    },
                    "error_message": {
                        "type": "string",
                        "description": "The error message",
                    },
                    "context": {
                        "type": "string",
                        "description": "Additional context about what the code should do",
                        "default": "",
                    },
                },
                "required": ["code", "error_message"],
            },
            "handler": fix_code,
        },
        {
            "name": "generate_code",
            "description": "Generate code from a natural language description.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the code should do",
                    },
                    "examples": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional input/output examples",
                        "default": [],
                    },
                },
                "required": ["description"],
            },
            "handler": generate_code,
        },
        {
            "name": "generate_tests",
            "description": "Generate unit tests for the given code.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to generate tests for",
                    },
                    "test_framework": {
                        "type": "string",
                        "description": "Test framework to use",
                        "enum": ["pytest", "unittest"],
                        "default": "pytest",
                    },
                },
                "required": ["code"],
            },
            "handler": generate_tests,
        },
        {
            "name": "explain_code",
            "description": "Explain what the given code does in detail.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to explain",
                    }
                },
                "required": ["code"],
            },
            "handler": explain_code,
        },
        {
            "name": "refactor_code",
            "description": "Refactor code for better quality, readability, or performance.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The code to refactor",
                    },
                    "goals": {
                        "type": "string",
                        "description": "Specific refactoring goals",
                        "default": "",
                    },
                },
                "required": ["code"],
            },
            "handler": refactor_code,
        },
    ]


def register_coding_tools(server: MCPServer) -> None:
    """Register coding tools with an MCP server."""
    for tool_def in build_coding_tool_definitions():
        server.register_tool(
            name=tool_def["name"],
            description=tool_def["description"],
            input_schema=tool_def["input_schema"],
            handler=tool_def["handler"],
        )

    logger.info("coding_tools_registered", tool_count=5)
