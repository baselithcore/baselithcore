"""MCP tool definitions for Baselithbot.

Each Playwright primitive is wrapped as an OpenClaw-style Tool. The
``eval_js_safe`` tool restricts execution to whitelisted snippets and
sanitizes user-supplied arguments via ``InputSanitizer``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.sanitization import InputSanitizer
from plugins.baselithbot.browser.agent import BaselithbotAgent
from plugins.baselithbot.browser.js_whitelist import ALLOWED_SNIPPETS
from plugins.baselithbot.types import BaselithbotTask
from plugins.browser_agent.types import BrowserAction, BrowserActionType

if TYPE_CHECKING:
    from core.mcp.server import MCPServer

logger = get_logger(__name__)


def build_baselithbot_tool_definitions(
    agent_factory: Any | None = None,
) -> list[dict[str, Any]]:
    """Return Baselithbot MCP tool definitions with lazy agent creation.

    Args:
        agent_factory: Optional callable returning a started ``BaselithbotAgent``.
            Useful for testing/dependency injection. Defaults to creating a
            new agent on first use.
    """
    shared_agent: BaselithbotAgent | None = None

    async def get_agent() -> BaselithbotAgent:
        nonlocal shared_agent
        if shared_agent is None:
            new_agent: BaselithbotAgent = agent_factory() if agent_factory else BaselithbotAgent()
            await new_agent.startup()
            shared_agent = new_agent
        return shared_agent

    async def baselithbot_navigate(url: str) -> dict[str, Any]:
        try:
            agent = await get_agent()
            state = await agent.backend.navigate(url)
            return {
                "status": "success",
                "url": state.url,
                "title": state.title,
                "visible_text": state.visible_text[:500],
            }
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="navigate", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def baselithbot_click(selector: str) -> dict[str, Any]:
        try:
            safe_selector = InputSanitizer.sanitize_query(selector, max_length=500)
            agent = await get_agent()
            ok = await agent.backend.click(safe_selector)
            return {"status": "success" if ok else "failed", "selector": safe_selector}
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="click", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def baselithbot_type(selector: str, text: str) -> dict[str, Any]:
        try:
            safe_selector = InputSanitizer.sanitize_query(selector, max_length=500)
            safe_text = InputSanitizer.sanitize_query(text, max_length=2000)
            agent = await get_agent()
            ok = await agent.backend.type_text(safe_selector, safe_text)
            return {
                "status": "success" if ok else "failed",
                "selector": safe_selector,
            }
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="type", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def baselithbot_scroll(direction: str = "down") -> dict[str, Any]:
        try:
            agent = await get_agent()
            ok = await agent.backend.execute_action(
                BrowserAction(
                    action_type=BrowserActionType.SCROLL,
                    value=direction,
                )
            )
            return {"status": "success" if ok else "failed", "direction": direction}
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="scroll", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def baselithbot_screenshot() -> dict[str, Any]:
        try:
            agent = await get_agent()
            screenshot = await agent.backend.screenshot()
            return {"status": "success", "screenshot_base64": screenshot}
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="screenshot", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def baselithbot_eval_js_safe(
        snippet_id: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if snippet_id not in ALLOWED_SNIPPETS:
            return {
                "status": "error",
                "error": f"snippet '{snippet_id}' not in whitelist",
            }

        sanitized_args: dict[str, str] = {}
        for key, value in (args or {}).items():
            if isinstance(value, int):
                sanitized_args[key] = str(int(value))
            else:
                clean = InputSanitizer.sanitize_query(str(value), max_length=500)
                sanitized_args[key] = json.dumps(clean)

        try:
            script = ALLOWED_SNIPPETS[snippet_id] % sanitized_args
        except KeyError as exc:
            return {"status": "error", "error": f"missing arg {exc}"}

        try:
            agent = await get_agent()
            page = agent.backend._page  # type: ignore[union-attr]
            if page is None:
                raise RuntimeError("Browser page not available")
            result = await page.evaluate(script)
            return {"status": "success", "snippet_id": snippet_id, "result": result}
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="eval_js_safe", error=str(exc))
            return {"status": "error", "error": str(exc)}

    async def baselithbot_run_task(
        goal: str,
        max_steps: int = 20,
        start_url: str | None = None,
    ) -> dict[str, Any]:
        try:
            agent = await get_agent()
            task = BaselithbotTask(goal=goal, max_steps=max_steps, start_url=start_url)
            result = await agent.execute(task)
            return result.model_dump()
        except Exception as exc:
            logger.error("baselithbot_tool_error", tool="run_task", error=str(exc))
            return {"status": "error", "error": str(exc)}

    return [
        {
            "name": "baselithbot_navigate",
            "description": "Navigate Baselithbot browser to a URL.",
            "input_schema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            "handler": baselithbot_navigate,
        },
        {
            "name": "baselithbot_click",
            "description": "Click an element by sanitized CSS selector.",
            "input_schema": {
                "type": "object",
                "properties": {"selector": {"type": "string"}},
                "required": ["selector"],
            },
            "handler": baselithbot_click,
        },
        {
            "name": "baselithbot_type",
            "description": "Type sanitized text into an input element.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["selector", "text"],
            },
            "handler": baselithbot_type,
        },
        {
            "name": "baselithbot_scroll",
            "description": "Scroll the page (up/down/top/bottom).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down", "top", "bottom"],
                        "default": "down",
                    }
                },
            },
            "handler": baselithbot_scroll,
        },
        {
            "name": "baselithbot_screenshot",
            "description": "Capture a base64 PNG screenshot of the current page.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": baselithbot_screenshot,
        },
        {
            "name": "baselithbot_eval_js_safe",
            "description": (
                "Execute a whitelisted JavaScript snippet by id. "
                "Arguments are sanitized via InputSanitizer."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "snippet_id": {
                        "type": "string",
                        "enum": list(ALLOWED_SNIPPETS.keys()),
                    },
                    "args": {"type": "object"},
                },
                "required": ["snippet_id"],
            },
            "handler": baselithbot_eval_js_safe,
        },
        {
            "name": "baselithbot_run_task",
            "description": "Execute an autonomous Baselithbot task end-to-end.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "max_steps": {"type": "integer", "default": 20},
                    "start_url": {"type": "string"},
                },
                "required": ["goal"],
            },
            "handler": baselithbot_run_task,
        },
    ]


def register_baselithbot_tools(server: MCPServer) -> None:
    """Register Baselithbot tools with an MCP server."""
    for tool_def in build_baselithbot_tool_definitions():
        server.register_tool(
            name=tool_def["name"],
            description=tool_def["description"],
            input_schema=tool_def["input_schema"],
            handler=tool_def["handler"],
        )
    logger.info("baselithbot_tools_registered", tool_count=7)


__all__ = ["build_baselithbot_tool_definitions", "register_baselithbot_tools"]
