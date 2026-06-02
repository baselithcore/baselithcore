"""MCP tools exposed by the Browser Agent plugin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .agent import BrowserAgent

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def build_browser_tool_definitions() -> list[dict[str, Any]]:
    """Return browser MCP tool definitions with lazy agent creation."""
    browser_agent: BrowserAgent | None = None

    async def get_browser_agent() -> BrowserAgent:
        nonlocal browser_agent
        if browser_agent is None:
            browser_agent = BrowserAgent(headless=True)
            await browser_agent.start()
        return browser_agent

    async def browser_navigate(url: str) -> dict[str, Any]:
        try:
            agent = await get_browser_agent()
            state = await agent.navigate(url)
            return {
                "status": "success",
                "url": state.url,
                "title": state.title,
                "screenshot": state.screenshot_base64[:100] + "...",
                "visible_text": state.visible_text[:500],
            }
        except Exception as exc:
            logger.error("browser_tool_error", tool="navigate", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def browser_click(selector: str) -> dict[str, Any]:
        try:
            agent = await get_browser_agent()
            success = await agent.click(selector)
            return {"status": "success" if success else "failed", "selector": selector}
        except Exception as exc:
            logger.error("browser_tool_error", tool="click", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def browser_type(selector: str, text: str) -> dict[str, Any]:
        try:
            agent = await get_browser_agent()
            success = await agent.type_text(selector, text)
            return {
                "status": "success" if success else "failed",
                "selector": selector,
                "text": text,
            }
        except Exception as exc:
            logger.error("browser_tool_error", tool="type", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def browser_screenshot() -> dict[str, Any]:
        try:
            agent = await get_browser_agent()
            screenshot = await agent.screenshot()
            return {
                "status": "success",
                "screenshot_base64": screenshot,
                "length": len(screenshot),
            }
        except Exception as exc:
            logger.error("browser_tool_error", tool="screenshot", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def browser_execute_task(task: str, max_steps: int = 10) -> dict[str, Any]:
        # Clamp caller-supplied step budget: each step drives a browser action
        # plus a vision call, so an unbounded value is a resource/token
        # exhaustion vector. Keep it in a sane [1, 50] range.
        max_steps = max(1, min(int(max_steps), 50))
        try:
            async with BrowserAgent(headless=True, max_steps=max_steps) as agent:
                result = await agent.execute_task(task)
                return {
                    "status": "success" if result.success else "failed",
                    "final_url": result.final_url,
                    "steps_taken": result.steps_taken,
                    "extracted_data": result.extracted_data,
                    "error": result.error,
                }
        except Exception as exc:
            logger.error("browser_tool_error", tool="execute_task", error=str(exc))
            return {"status": "error", "error": str(exc)}

    return [
        {
            "name": "browser_navigate",
            "description": "Navigate browser to a URL and return screenshot + page info",
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to navigate to",
                    }
                },
                "required": ["url"],
            },
            "handler": browser_navigate,
        },
        {
            "name": "browser_click",
            "description": "Click an element on the page by CSS selector",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {
                        "type": "string",
                        "description": "CSS selector of element to click",
                    }
                },
                "required": ["selector"],
            },
            "handler": browser_click,
        },
        {
            "name": "browser_type",
            "description": "Type text into an input field",
            "input_schema": {
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
            "handler": browser_type,
        },
        {
            "name": "browser_screenshot",
            "description": "Take a screenshot of the current page",
            "input_schema": {"type": "object", "properties": {}},
            "handler": browser_screenshot,
        },
        {
            "name": "browser_execute_task",
            "description": "Execute an autonomous browser task using natural language.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Natural language description of the task",
                    },
                    "max_steps": {
                        "type": "integer",
                        "description": "Maximum steps before giving up (clamped to 1-50)",
                        "default": 10,
                        "minimum": 1,
                        "maximum": 50,
                    },
                },
                "required": ["task"],
            },
            "handler": browser_execute_task,
        },
    ]


def register_browser_tools(server: "MCPServer") -> None:
    """Register browser tools with an MCP server."""
    for tool_def in build_browser_tool_definitions():
        server.register_tool(
            name=tool_def["name"],
            description=tool_def["description"],
            input_schema=tool_def["input_schema"],
            handler=tool_def["handler"],
        )

    logger.info("browser_tools_registered", tool_count=5)
