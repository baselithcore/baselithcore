"""Browser Agent plugin implementation."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlparse

from core.observability.logging import get_logger
from core.services.vision.models import ImageContent, VisionCapability, VisionRequest
from core.services.vision.service import VisionService

from .types import BrowserAction, BrowserActionType, BrowserAgentResult, PageState

logger = get_logger(__name__)

_JQUERY_CONTAINS = re.compile(r":contains\(\s*['\"]([^'\"]+)['\"]\s*\)")

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_BLOCKED_HOSTNAMES = frozenset({"localhost", "broadcasthost"})


def _hostname_is_blocked(hostname: str) -> bool:
    """Return True for hostnames that resolve to internal/loopback ranges."""
    if not hostname:
        return True
    lowered = hostname.lower().strip(".")
    if lowered in _BLOCKED_HOSTNAMES or lowered.endswith(".localhost"):
        return True
    try:
        addr = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def _ssrf_guard_disabled() -> bool:
    """Return True when ``BASELITH_BROWSER_ALLOW_INTERNAL`` is truthy."""
    raw = os.environ.get("BASELITH_BROWSER_ALLOW_INTERNAL", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def assert_navigation_allowed(url: str) -> None:
    """Raise ``ValueError`` when ``url`` targets an internal/loopback resource.

    Override with ``BASELITH_BROWSER_ALLOW_INTERNAL=true`` for trusted local use.
    """
    if _ssrf_guard_disabled():
        return
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"Refusing to navigate: scheme '{scheme}' not allowed")
    hostname = parsed.hostname or ""
    if _hostname_is_blocked(hostname):
        raise ValueError(
            f"Refusing to navigate: '{hostname}' resolves to a blocked range"
        )


def _normalize_selector(selector: str) -> str:
    """Translate jQuery-style ``:contains("X")`` to Playwright ``:has-text("X")``.

    Vision models frequently emit jQuery-flavored selectors that Playwright's
    query engine rejects. Rewriting here keeps the click/fill call sites free
    of model-specific quirks.
    """
    return _JQUERY_CONTAINS.sub(lambda m: f':has-text("{m.group(1)}")', selector)


class BrowserAgent:
    """
    Autonomous browser agent with visual reasoning.

    Uses Playwright for browser control and VisionService for
    understanding page content and making decisions.
    """

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

For extracting data (populate `data` with the actual extracted values — keys are field names, values can be strings, numbers, or arrays):
{"action": "extract", "data": {"titles": ["Repo A", "Repo B"], "stars": [1234, 567]}, "reasoning": "extracted repository cards visible on the page"}

When task is complete:
{"action": "done", "reasoning": "task completed because..."}

If task cannot be completed:
{"action": "fail", "reasoning": "failed because..."}

IMPORTANT:
- Always analyze the screenshot before acting
- Use CSS selectors when elements are clearly identifiable
- Use coordinates when selectors are not reliable
- NEVER use jQuery-only syntax like `:contains("…")`. Playwright rejects it. For text matching use `:has-text("…")`, `text="…"`, or match by visible attributes (e.g. `button[aria-label='Accept']`).
- When unsure about a selector, emit coordinates instead — they never fail to parse.
- If the previous step logged `browser_action_failed`, DO NOT retry the same selector. Either switch to coordinates or pick a different element.
- Maximum 20 steps per task
- If stuck, try alternative approaches
- When the task is a list/collection extraction, extract every item visible in the current viewport, then issue a `scroll` action to reveal more items and extract again. Repeat scroll+extract until the page stops producing new items, then emit `done`.
- Prior `extract` outputs are remembered and de-duplicated automatically — just keep emitting what you currently see."""

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

    async def __aenter__(self) -> "BrowserAgent":
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

        prompt = f"""{self.SYSTEM_PROMPT}

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
