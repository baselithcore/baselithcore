"""
Browser Tools for MCP.

Exposes browser automation capabilities as MCP-compatible tools.

Usage:
    from core.mcp import MCPServer
    from core.agents.browser_tools import register_browser_tools

    server = MCPServer()
    register_browser_tools(server)
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.observability.logging import get_logger

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def register_browser_tools(server: MCPServer) -> None:
    """
    Register browser tools with an MCP server.

    Args:
        server: MCP server instance
    """
    # Lazy import to avoid Playwright requirement at module level
    _browser_agent = None

    async def get_browser_agent():
        nonlocal _browser_agent
        if _browser_agent is None:
            from core.agents.browser_agent import BrowserAgent

            _browser_agent = BrowserAgent(headless=True)
            await _browser_agent.start()
        return _browser_agent

    @server.tool(
        name="browser_navigate",
        description="Navigate browser to a URL and return screenshot + page info",
        input_schema={
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to navigate to",
                },
            },
            "required": ["url"],
        },
    )
    async def browser_navigate(url: str) -> dict[str, Any]:
        """Navigate to URL."""
        try:
            agent = await get_browser_agent()
            state = await agent.navigate(url)

            return {
                "status": "success",
                "url": state.url,
                "title": state.title,
                "screenshot": state.screenshot_base64[:100]
                + "...",  # Truncated for display
                "visible_text": state.visible_text[:500],
            }
        except Exception as e:
            logger.error("browser_tool_error", tool="navigate", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="browser_click",
        description="Click an element on the page by CSS selector",
        input_schema={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of element to click",
                },
            },
            "required": ["selector"],
        },
    )
    async def browser_click(selector: str) -> dict[str, Any]:
        """Click element."""
        try:
            agent = await get_browser_agent()
            success = await agent.click(selector)

            return {
                "status": "success" if success else "failed",
                "selector": selector,
            }
        except Exception as e:
            logger.error("browser_tool_error", tool="click", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="browser_type",
        description="Type text into an input field",
        input_schema={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of input element",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type",
                },
            },
            "required": ["selector", "text"],
        },
    )
    async def browser_type(selector: str, text: str) -> dict[str, Any]:
        """Type into element."""
        try:
            agent = await get_browser_agent()
            success = await agent.type_text(selector, text)

            return {
                "status": "success" if success else "failed",
                "selector": selector,
                "text": text,
            }
        except Exception as e:
            logger.error("browser_tool_error", tool="type", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="browser_screenshot",
        description="Take a screenshot of the current page",
        input_schema={
            "type": "object",
            "properties": {},
        },
    )
    async def browser_screenshot() -> dict[str, Any]:
        """Take screenshot."""
        try:
            agent = await get_browser_agent()
            screenshot = await agent.screenshot()

            return {
                "status": "success",
                "screenshot_base64": screenshot,
                "length": len(screenshot),
            }
        except Exception as e:
            logger.error("browser_tool_error", tool="screenshot", error=str(e))
            return {"status": "error", "error": str(e)}

    @server.tool(
        name="browser_execute_task",
        description="Execute an autonomous browser task using natural language. The agent will navigate, click, and type to complete the goal.",
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Natural language description of the task (e.g., 'Go to google.com and search for Python tutorials')",
                },
                "max_steps": {
                    "type": "integer",
                    "description": "Maximum steps before giving up",
                    "default": 10,
                },
            },
            "required": ["task"],
        },
    )
    async def browser_execute_task(task: str, max_steps: int = 10) -> dict[str, Any]:
        """Execute autonomous browser task."""
        try:
            from core.agents.browser_agent import BrowserAgent

            async with BrowserAgent(headless=True, max_steps=max_steps) as agent:
                result = await agent.execute_task(task)

                return {
                    "status": "success" if result.success else "failed",
                    "final_url": result.final_url,
                    "steps_taken": result.steps_taken,
                    "extracted_data": result.extracted_data,
                    "error": result.error,
                }
        except Exception as e:
            logger.error("browser_tool_error", tool="execute_task", error=str(e))
            return {"status": "error", "error": str(e)}

    logger.info("browser_tools_registered", tool_count=5)
