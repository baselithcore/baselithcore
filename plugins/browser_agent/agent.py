"""Browser Agent plugin implementation."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from core.observability.logging import get_logger
from core.services.vision.models import ImageContent, VisionCapability, VisionRequest
from core.services.vision.service import VisionService

from ._guards import (
    _hostname_is_blocked,
    _hostname_resolves_to_internal,
    _ip_is_internal,
    _normalize_selector,
    _ssrf_guard_disabled,
    _url_is_blocked,
    assert_navigation_allowed,
)
from .prompts import BROWSER_SYSTEM_PROMPT
from .types import BrowserAction, BrowserActionType, BrowserAgentResult, PageState

logger = get_logger(__name__)

# Re-exported SSRF guard helpers — callers/tests import these from this module
# rather than reaching into the private ``_guards`` submodule.
__all__ = [
    "BrowserAgent",
    "_hostname_is_blocked",
    "_hostname_resolves_to_internal",
    "_ip_is_internal",
    "_ssrf_guard_disabled",
    "_url_is_blocked",
    "assert_navigation_allowed",
]


class BrowserAgent:
    """
    Autonomous browser agent with visual reasoning.

    Uses Playwright for browser control and VisionService for
    understanding page content and making decisions.
    """

    def __init__(
        self,
        headless: bool = True,
        viewport_width: int = 1280,
        viewport_height: int = 720,
        max_steps: int = 20,
        vision_service: VisionService | None = None,
        context_options: dict[str, Any] | None = None,
    ) -> None:
        self.headless = headless
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.max_steps = max_steps
        self.vision = vision_service or VisionService()
        self.context_options = dict(context_options or {})

        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._playwright: Any | None = None

        self._vision_tokens_total: int = 0
        self._vision_calls: int = 0
        self._last_vision_model: str | None = None
        self._last_vision_provider: str | None = None

    async def __aenter__(self) -> BrowserAgent:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
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
        context_options = {
            "viewport": {"width": self.viewport_width, "height": self.viewport_height},
            **self.context_options,
        }
        self._context = await self._browser.new_context(**context_options)

        # Re-validate every navigation (including server-driven redirects, which
        # Playwright follows internally and which bypass the one-shot pre-goto
        # check). Aborts navigations to internal/blocked hosts at the network
        # layer. DNS resolution runs off the event loop.
        if not _ssrf_guard_disabled():
            await self._context.route("**/*", self._ssrf_route_guard)

        self._page = await self._context.new_page()

        logger.info(
            "browser_agent_started",
            headless=self.headless,
            viewport=f"{self.viewport_width}x{self.viewport_height}",
        )

    async def _ssrf_route_guard(self, route: Any, request: Any) -> None:
        """Playwright route handler: abort requests to blocked/internal hosts.

        Scoped to navigation requests (main frame + sub-frame document loads,
        which is where redirects land) to keep asset loading fast. DNS
        resolution runs in a worker thread so it never blocks the event loop.
        """
        try:
            is_nav = bool(request.is_navigation_request())
        except Exception:
            is_nav = True  # fail-closed: treat unknown as a navigation
        if not is_nav:
            await route.continue_()
            return
        try:
            blocked = await asyncio.to_thread(
                _url_is_blocked, request.url, resolve_dns=True
            )
        except Exception:
            blocked = True  # fail-closed
        if blocked:
            logger.warning("browser_ssrf_blocked_navigation", url=request.url)
            await route.abort("blockedbyclient")
            return
        await route.continue_()

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
        """Execute a browser action."""
        if not self._page:
            raise RuntimeError("Browser not started")

        selector = _normalize_selector(action.selector) if action.selector else None
        try:
            if action.action_type == BrowserActionType.NAVIGATE:
                target_url = action.value or ""
                assert_navigation_allowed(target_url)
                await self._page.goto(target_url, wait_until="domcontentloaded")
            elif action.action_type == BrowserActionType.CLICK:
                if selector:
                    try:
                        await self._page.click(selector, timeout=5000)
                    except Exception as sel_exc:
                        if action.coordinates:
                            logger.info(
                                "browser_click_selector_fallback",
                                selector=selector,
                                error=str(sel_exc),
                            )
                            x = int(action.coordinates[0] * self.viewport_width / 100)
                            y = int(action.coordinates[1] * self.viewport_height / 100)
                            await self._page.mouse.click(x, y)
                        else:
                            raise
                elif action.coordinates:
                    x = int(action.coordinates[0] * self.viewport_width / 100)
                    y = int(action.coordinates[1] * self.viewport_height / 100)
                    await self._page.mouse.click(x, y)
            elif action.action_type == BrowserActionType.TYPE:
                if selector:
                    await self._page.fill(selector, action.value or "")
                    if "search" in selector.lower():
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
                wait_time = float(action.value or 1)
                await asyncio.sleep(wait_time)

            logger.info(
                "browser_action_executed",
                action=action.action_type.value,
                selector=selector,
                value=action.value[:50] if action.value else None,
            )
            return True
        except Exception as exc:
            logger.warning(
                "browser_action_failed",
                action=action.action_type.value,
                error=str(exc),
            )
            return False

    async def decide_next_action(
        self, task: str, page_state: PageState, history: list[str]
    ) -> BrowserAction:
        """Use vision + LLM to decide the next action."""
        history_text = "\n".join(f"- {h}" for h in history[-5:]) if history else "None"

        prompt = f"""{BROWSER_SYSTEM_PROMPT}

---

Task: {task}

Current URL: {page_state.url}
Page Title: {page_state.title}

Recent actions:
{history_text}

Analyze the screenshot and decide the next action to complete the task.
Respond ONLY with valid JSON matching one of the schemas above."""

        request = VisionRequest(
            prompt=prompt,
            images=[ImageContent.from_base64(page_state.screenshot_base64)],
            capability=VisionCapability.SCREENSHOT_ANALYSIS,
            json_mode=True,
            max_tokens=500,
        )

        try:
            response = await self.vision.analyze(request)
            self._vision_tokens_total += int(response.tokens_used or 0)
            self._vision_calls += 1
            self._last_vision_model = response.model or self._last_vision_model
            self._last_vision_provider = response.provider or self._last_vision_provider
            result = response.as_json
            if not result:
                import json

                try:
                    result = json.loads(response.content)
                except json.JSONDecodeError:
                    logger.warning(
                        "browser_decide_non_json",
                        content=response.content[:500],
                        provider=response.provider,
                        model=response.model,
                    )
                    result = None

            if not result:
                raise ValueError(
                    f"Empty/invalid JSON from vision ({response.provider}/"
                    f"{response.model}): {response.content[:200]!r}"
                )

            logger.info(
                "browser_decide_raw",
                raw=result,
                provider=response.provider,
                model=response.model,
            )

            action_str = result.get("action") or "fail"
            try:
                action_type = BrowserActionType(action_str)
            except ValueError:
                logger.warning(
                    "browser_decide_unknown_action",
                    action=action_str,
                    raw=result,
                )
                return BrowserAction(
                    action_type=BrowserActionType.FAIL,
                    reasoning=(
                        f"Vision returned unknown action={action_str!r}; "
                        f"full response: {result}"
                    ),
                )

            raw_value = result.get("value")
            value_str: str | None = None
            data_payload: dict[str, Any] | None = None
            if isinstance(raw_value, dict):
                data_payload = raw_value
            elif isinstance(raw_value, list):
                data_payload = {"items": raw_value}
            elif raw_value is not None:
                value_str = str(raw_value)
            if value_str is None:
                url_val = result.get("url")
                if url_val is not None:
                    value_str = str(url_val)
            explicit_data = result.get("data")
            if isinstance(explicit_data, dict):
                data_payload = (
                    {**(data_payload or {}), **explicit_data}
                    if data_payload
                    else explicit_data
                )

            return BrowserAction(
                action_type=action_type,
                selector=result.get("selector"),
                value=value_str,
                coordinates=tuple(result["coordinates"])
                if "coordinates" in result
                else None,
                reasoning=result.get("reasoning") or result.get("explanation") or "",
                data=data_payload,
            )
        except Exception as exc:
            logger.error("browser_decide_error", error=str(exc))
            return BrowserAction(
                action_type=BrowserActionType.FAIL,
                reasoning=f"Failed to decide next action: {exc}",
            )

    async def execute_task(self, task: str) -> BrowserAgentResult:
        """Execute a browser automation task."""
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
                state = await self.get_page_state()
                screenshots.append(state.screenshot_base64)

                action = await self.decide_next_action(task, state, history)
                history.append(f"{action.action_type.value}: {action.reasoning}")

                logger.info(
                    "browser_step",
                    step=steps,
                    action=action.action_type.value,
                    reasoning=action.reasoning[:100],
                )

                if action.action_type == BrowserActionType.DONE:
                    return BrowserAgentResult(
                        success=True,
                        final_url=state.url,
                        steps_taken=steps,
                        extracted_data=extracted_data,
                        screenshots=screenshots[-3:],
                    )
                if action.action_type == BrowserActionType.FAIL:
                    return BrowserAgentResult(
                        success=False,
                        final_url=state.url,
                        steps_taken=steps,
                        error=action.reasoning,
                        screenshots=screenshots[-3:],
                    )
                if action.action_type == BrowserActionType.EXTRACT:
                    fields = action.value.split(",") if action.value else []
                    for field_name in fields:
                        extracted_data[field_name.strip()] = (
                            f"[extracted from {state.url}]"
                        )
                    continue

                success = await self.execute_action(action)
                if not success:
                    await asyncio.sleep(1)

                await asyncio.sleep(0.5)

            return BrowserAgentResult(
                success=False,
                final_url=self._page.url if self._page else "",
                steps_taken=steps,
                error=f"Max steps ({self.max_steps}) reached",
                screenshots=screenshots[-3:],
            )
        except Exception as exc:
            logger.exception("browser_task_error", error=str(exc))
            return BrowserAgentResult(
                success=False,
                final_url=self._page.url if self._page else "",
                steps_taken=steps,
                error=str(exc),
                screenshots=screenshots[-3:] if screenshots else [],
            )

    async def navigate(self, url: str) -> PageState:
        """Navigate to a URL and return page state."""
        assert_navigation_allowed(url)

        if not self._page:
            await self.start()
            assert self._page  # nosec B101

        await self._page.goto(url, wait_until="domcontentloaded")
        return await self.get_page_state()

    async def screenshot(self) -> str:
        """Take a screenshot and return base64."""
        state = await self.get_page_state()
        return state.screenshot_base64

    async def click(self, selector: str) -> bool:
        """Click an element by selector."""
        return await self.execute_action(
            BrowserAction(action_type=BrowserActionType.CLICK, selector=selector)
        )

    async def type_text(self, selector: str, text: str) -> bool:
        """Type text into an element."""
        return await self.execute_action(
            BrowserAction(
                action_type=BrowserActionType.TYPE,
                selector=selector,
                value=text,
            )
        )
