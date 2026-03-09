"""
Browser Agent.

An autonomous agent that controls a web browser using Playwright,
with visual reasoning capabilities via the Vision service.

Implements the ReAct (Reason + Act) loop:
1. Observe: Take screenshot, analyze page state
2. Think: Reason about next action using LLM
3. Act: Execute browser action (click, type, navigate)
4. Repeat until goal is achieved

Usage:
    from core.agents import BrowserAgent

    async with BrowserAgent() as agent:
        result = await agent.execute_task(
            "Go to google.com and search for 'Python tutorials'"
        )
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from core.agents.browser_types import (
    BrowserAction,
    BrowserActionType,
    BrowserAgentResult,
    PageState,
)

from core.observability.logging import get_logger
from core.services.vision.models import ImageContent
from core.services.vision.service import VisionService

logger = get_logger(__name__)


# ============================================================================
# Browser Agent
# ============================================================================


class BrowserAgent:
    """
    Autonomous browser agent with visual reasoning.

    Uses Playwright for browser control and VisionService for
    understanding page content and making decisions.
    """

    # System prompt for the agent
    SYSTEM_PROMPT = """You are a browser automation agent. You control a web browser to complete user tasks.

For each step, you will receive:
1. A screenshot of the current page
2. The current URL and page title
3. Your task goal

You must respond with a JSON action:

For navigation:
{"action": "navigate", "value": "https://example.com", "reasoning": "why"}

For clicking (prefer selectors when visible):
{"action": "click", "selector": "button.submit", "reasoning": "why"}
OR with coordinates (x, y as percentage 0-100):
{"action": "click", "coordinates": [50, 75], "reasoning": "clicking center-bottom area"}

For typing:
{"action": "type", "selector": "input[name='search']", "value": "search text", "reasoning": "why"}

For scrolling:
{"action": "scroll", "value": "down", "reasoning": "why"}  // up, down, top, bottom

For waiting:
{"action": "wait", "value": "2", "reasoning": "waiting 2 seconds for page load"}

For extracting data:
{"action": "extract", "value": "price,title,description", "reasoning": "extracting product info"}

When task is complete:
{"action": "done", "reasoning": "task completed because..."}

If task cannot be completed:
{"action": "fail", "reasoning": "failed because..."}

IMPORTANT:
- Always analyze the screenshot before acting
- Use CSS selectors when elements are clearly identifiable
- Use coordinates when selectors are not reliable
- Maximum 20 steps per task
- If stuck, try alternative approaches"""

    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        max_steps: int = 20,
        vision_service: VisionService | None = None,
    ) -> None:
        """
        Initialize browser agent.

        Args:
            headless: Run browser in headless mode
            viewport_width: Browser viewport width
            viewport_height: Browser viewport height
            max_steps: Maximum steps before giving up
            vision_service: Optional VisionService instance
        """
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.max_steps = max_steps
        self.vision = vision_service or VisionService()

        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._playwright: Any | None = None

    async def __aenter__(self) -> BrowserAgent:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.stop()

    async def start(self) -> None:
        """Start the browser."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright required: pip install playwright && playwright install"
            ) from None

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context(
            viewport={"width": self.viewport_width, "height": self.viewport_height}
        )
        self._page = await self._context.new_page()

        logger.info(
            "browser_agent_started",
            headless=self.headless,
            viewport=f"{self.viewport_width}x{self.viewport_height}",
        )

    async def stop(self) -> None:
        """Stop the browser."""
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

        logger.info("browser_agent_stopped")

    async def get_page_state(self) -> PageState:
        """Get current page state with screenshot."""
        if not self._page:
            raise RuntimeError("Browser not started")

        screenshot_bytes = await self._page.screenshot(type="png")
        screenshot_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        # Try to get visible text
        try:
            visible_text = await self._page.evaluate(
                "() => document.body.innerText.substring(0, 2000)"
            )
        except Exception:
            visible_text = ""

        return PageState(
            url=self._page.url,
            title=await self._page.title(),
            screenshot_base64=screenshot_base64,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            visible_text=visible_text,
        )

    async def execute_action(self, action: BrowserAction) -> bool:
        """
        Execute a browser action.

        Returns:
            True if action succeeded, False otherwise
        """
        if not self._page:
            raise RuntimeError("Browser not started")

        try:
            if action.action_type == BrowserActionType.NAVIGATE:
                await self._page.goto(action.value or "", wait_until="domcontentloaded")

            elif action.action_type == BrowserActionType.CLICK:
                if action.selector:
                    await self._page.click(action.selector, timeout=5000)
                elif action.coordinates:
                    x = int(action.coordinates[0] * self.viewport_width / 100)
                    y = int(action.coordinates[1] * self.viewport_height / 100)
                    await self._page.mouse.click(x, y)

            elif action.action_type == BrowserActionType.TYPE:
                if action.selector:
                    await self._page.fill(action.selector, action.value or "")
                    # Press Enter if it looks like a search
                    if "search" in action.selector.lower():
                        await self._page.keyboard.press("Enter")

            elif action.action_type == BrowserActionType.SCROLL:
                direction = action.value or "down"
                if direction == "down":
                    await self._page.evaluate("window.scrollBy(0, 500)")
                elif direction == "up":
                    await self._page.evaluate("window.scrollBy(0, -500)")
                elif direction == "top":
                    await self._page.evaluate("window.scrollTo(0, 0)")
                elif direction == "bottom":
                    await self._page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )

            elif action.action_type == BrowserActionType.WAIT:
                wait_time = float(action.value or 1) * 1000
                await asyncio.sleep(wait_time / 1000)

            logger.info(
                "browser_action_executed",
                action=action.action_type.value,
                selector=action.selector,
                value=action.value[:50] if action.value else None,
            )
            return True

        except Exception as e:
            logger.warning(
                "browser_action_failed",
                action=action.action_type.value,
                error=str(e),
            )
            return False

    async def decide_next_action(
        self, task: str, page_state: PageState, history: list[str]
    ) -> BrowserAction:
        """
        Use vision + LLM to decide the next action.

        Args:
            task: The goal to achieve
            page_state: Current page state
            history: List of previous actions

        Returns:
            Next action to execute
        """
        # Build the prompt
        history_text = "\n".join(f"- {h}" for h in history[-5:]) if history else "None"

        prompt = f"""Task: {task}

Current URL: {page_state.url}
Page Title: {page_state.title}

Recent actions:
{history_text}

Analyze the screenshot and decide the next action to complete the task.
Respond ONLY with valid JSON."""

        # Use vision to analyze and decide
        from core.services.vision.models import VisionRequest, VisionCapability

        request = VisionRequest(
            prompt=prompt,
            images=[ImageContent.from_base64(page_state.screenshot_base64)],
            capability=VisionCapability.SCREENSHOT_ANALYSIS,
            json_mode=True,
            max_tokens=500,
        )

        try:
            response = await self.vision.analyze(request)
            result = response.as_json

            if not result:
                # Try to parse manually
                import json

                result = json.loads(response.content)

            if not result:
                raise ValueError("Empty result from vision service")

            action_type = BrowserActionType(result.get("action", "fail"))

            return BrowserAction(
                action_type=action_type,
                selector=result.get("selector"),
                value=result.get("value"),
                coordinates=tuple(result["coordinates"])
                if "coordinates" in result
                else None,
                reasoning=result.get("reasoning", ""),
            )

        except Exception as e:
            logger.error("browser_decide_error", error=str(e))
            return BrowserAction(
                action_type=BrowserActionType.FAIL,
                reasoning=f"Failed to decide next action: {e}",
            )

    async def execute_task(self, task: str) -> BrowserAgentResult:
        """
        Execute a browser automation task.

        Args:
            task: Natural language description of the task

        Returns:
            Result of task execution
        """
        if not self._page:
            await self.start()

        logger.info("browser_task_start", task=task[:100])

        history: list[str] = []
        screenshots: list[str] = []
        extracted_data: dict[str, Any] = {}
        steps = 0

        try:
            while steps < self.max_steps:
                steps += 1

                # Get current state
                state = await self.get_page_state()
                screenshots.append(state.screenshot_base64)

                # Decide next action
                action = await self.decide_next_action(task, state, history)
                history.append(f"{action.action_type.value}: {action.reasoning}")

                logger.info(
                    "browser_step",
                    step=steps,
                    action=action.action_type.value,
                    reasoning=action.reasoning[:100],
                )

                # Check for completion
                if action.action_type == BrowserActionType.DONE:
                    return BrowserAgentResult(
                        success=True,
                        final_url=state.url,
                        steps_taken=steps,
                        extracted_data=extracted_data,
                        screenshots=screenshots[-3:],  # Keep last 3
                    )

                if action.action_type == BrowserActionType.FAIL:
                    return BrowserAgentResult(
                        success=False,
                        final_url=state.url,
                        steps_taken=steps,
                        error=action.reasoning,
                        screenshots=screenshots[-3:],
                    )

                # Handle extraction
                if action.action_type == BrowserActionType.EXTRACT:
                    fields = action.value.split(",") if action.value else []
                    for field_name in fields:
                        extracted_data[field_name.strip()] = (
                            f"[extracted from {state.url}]"
                        )
                    continue

                # Execute action
                success = await self.execute_action(action)
                if not success:
                    # Wait and retry
                    await asyncio.sleep(1)

                # Wait for page to settle
                await asyncio.sleep(0.5)

            # Max steps reached
            return BrowserAgentResult(
                success=False,
                final_url=self._page.url if self._page else "",
                steps_taken=steps,
                error=f"Max steps ({self.max_steps}) reached",
                screenshots=screenshots[-3:],
            )

        except Exception as e:
            logger.exception("browser_task_error", error=str(e))
            return BrowserAgentResult(
                success=False,
                final_url=self._page.url if self._page else "",
                steps_taken=steps,
                error=str(e),
                screenshots=screenshots[-3:] if screenshots else [],
            )

    # -------------------------------------------------------------------------
    # Convenience Methods
    # -------------------------------------------------------------------------

    async def navigate(self, url: str) -> PageState:
        """Navigate to a URL and return page state."""
        if not self._page:
            await self.start()
            assert self._page  # nosec B101  # for mypy

        await self._page.goto(url, wait_until="domcontentloaded")
        return await self.get_page_state()

    async def screenshot(self) -> str:
        """Take a screenshot and return base64."""
        state = await self.get_page_state()
        return state.screenshot_base64

    async def click(self, selector: str) -> bool:
        """Click an element by selector."""
        action = BrowserAction(action_type=BrowserActionType.CLICK, selector=selector)
        return await self.execute_action(action)

    async def type_text(self, selector: str, text: str) -> bool:
        """Type text into an element."""
        action = BrowserAction(
            action_type=BrowserActionType.TYPE, selector=selector, value=text
        )
        return await self.execute_action(action)
