"""
Coding Tools for MCP.

Exposes coding agent capabilities as MCP-compatible tools.

Usage:
    from core.mcp import MCPServer
    from core.agents.coding_tools import register_coding_tools

    server = MCPServer()
    register_coding_tools(server)
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def register_coding_tools(server: MCPServer) -> None:
    """
    Register coding tools with an MCP server.

    Args:
        server: MCP server instance
    """
    _coding_agent = None

    async def get_coding_agent():
        nonlocal _coding_agent
        if _coding_agent is None:
            from core.agents.coding import CodingAgent

            _coding_agent = CodingAgent()
        return _coding_agent

    @server.tool(
        name="fix_code",
        description="Fix buggy code using an auto-debug loop. Analyzes errors and iteratively fixes until success.",
        input_schema={
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
    )
    async def fix_code(
        code: str, error_message: str, context: str = ""
    ) -> dict[str, Any]:
        """Fix buggy code."""
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
        except Exception as e:
            logger.error("coding_tool_error", tool="fix_code", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="generate_code",
        description="Generate code from a natural language description.",
        input_schema={
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
    )
    async def generate_code(
        description: str, examples: list[str] | None = None
    ) -> dict[str, Any]:
        """Generate code from description."""
        try:
            agent = await get_coding_agent()
            result = await agent.generate_code(description, examples)

            return {
                "status": "success" if result.success else "failed",
                "generated_code": result.final_code,
                "error": result.error,
            }
        except Exception as e:
            logger.error("coding_tool_error", tool="generate_code", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="generate_tests",
        description="Generate unit tests for the given code.",
        input_schema={
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
    )
    async def generate_tests(
        code: str, test_framework: str = "pytest"
    ) -> dict[str, Any]:
        """Generate tests for code."""
        try:
            agent = await get_coding_agent()
            result = await agent.generate_tests(code, test_framework)

            return {
                "status": "success" if result.success else "failed",
                "test_code": result.final_code,
                "error": result.error,
            }
        except Exception as e:
            logger.error("coding_tool_error", tool="generate_tests", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="explain_code",
        description="Explain what the given code does in detail.",
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The code to explain",
                },
            },
            "required": ["code"],
        },
    )
    async def explain_code(code: str) -> dict[str, Any]:
        """Explain code."""
        try:
            agent = await get_coding_agent()
            explanation = await agent.explain_code(code)

            return {
                "status": "success",
                "explanation": explanation,
            }
        except Exception as e:
            logger.error("coding_tool_error", tool="explain_code", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="refactor_code",
        description="Refactor code for better quality, readability, or performance.",
        input_schema={
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
    )
    async def refactor_code(code: str, goals: str = "") -> dict[str, Any]:
        """Refactor code."""
        try:
            agent = await get_coding_agent()
            result = await agent.refactor_code(code, goals)

            return {
                "status": "success" if result.success else "failed",
                "refactored_code": result.final_code,
                "explanation": result.explanation,
                "error": result.error,
            }
        except Exception as e:
            logger.error("coding_tool_error", tool="refactor_code", error=str(e))
            return {"status": "error", "error": str(e)}

    logger.info("coding_tools_registered", tool_count=5)
