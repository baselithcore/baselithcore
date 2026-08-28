"""Browser Agent plugin implementation.

``plugins.browser_agent.agent`` is a frozen public import path: the
``core.agents.browser_agent`` shim and ``scripts/check_architecture_boundaries.py``
both encode it, and the SSRF suite patches ``agent.socket.getaddrinfo``. The
``noqa: F401`` imports and the SSRF re-exports below keep that historic module
surface bound here.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import socket  # noqa: F401
from typing import Any
from urllib.parse import urlparse

from core.observability.logging import get_logger
from core.services.vision.models import ImageContent, VisionCapability, VisionRequest
from core.services.vision.service import VisionService

from .actions import build_action as _build_action
from .actions import normalize_selector as _normalize_selector
from .prompts import BROWSER_SYSTEM_PROMPT as _BROWSER_SYSTEM_PROMPT
from .ssrf import (  # noqa: F401
    SsrfVerdictCache,
    _hostname_is_blocked,
    _hostname_resolves_to_internal,
    _ip_is_internal,
    _ssrf_guard_disabled,
    _url_is_blocked,
    assert_navigation_allowed,
)
from .types import BrowserAction, BrowserActionType, BrowserAgentResult, PageState

logger = get_logger(__name__)


class BrowserAgent:
    """
    Autonomous browser agent with visual reasoning.

    Uses Playwright for browser control and VisionService for
    understanding page content and making decisions.
    """

    SYSTEM_PROMPT = _BROWSER_SYSTEM_PROMPT

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

        # Per-host SSRF verdict cache for the page(s) loaded in this browser
        # context (re-initialized on every start()). Without it, the route
        # guard added in start() would pay a DNS lookup for every single
        # sub-resource request (images, scripts, fetch/XHR) on top of
        # navigations. A verdict expires on a TTL *and* on every top-level
        # navigation, so the rebinding window is bounded even on a
        # single-page app that never navigates. The window cannot be closed
        # in-process — Chromium re-resolves independently; see the warning in
        # plugins/browser_agent/ssrf.py for the proxy/network mitigations.
        self._ssrf_host_cache = SsrfVerdictCache()

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
        self._ssrf_host_cache = SsrfVerdictCache()
        guard_enabled = not _ssrf_guard_disabled()

        # Re-validate every request the page issues — navigation (including
        # server-driven redirects, which Playwright follows internally and
        # bypass the one-shot pre-goto check) *and* sub-resource loads
        # (scripts, images, fetch/XHR) — so a page cannot smuggle an SSRF
        # probe through a same-origin asset request. DNS resolution runs off
        # the event loop and its verdict is cached per host (see
        # self._ssrf_host_cache).
        if guard_enabled:
            await self._context.route("**/*", self._ssrf_route_guard)

        self._page = await self._context.new_page()
        if guard_enabled:
            self._page.on("framenavigated", self._reset_ssrf_host_cache_on_navigation)

        logger.info(
            "browser_agent_started",
            headless=self.headless,
            viewport=f"{self.viewport_width}x{self.viewport_height}",
        )

    def _reset_ssrf_host_cache_on_navigation(self, frame: Any) -> None:
        """Clear the per-host SSRF verdict cache on a new top-level navigation.

        Bounds the cache in ``_ssrf_route_guard`` to the currently loaded
        page: without this reset, a host that DNS-rebinds to an internal
        address after an earlier, unrelated page load in this context would
        keep passing on a stale verdict. Sub-frame (iframe) navigations are
        ignored — only a main-frame navigation starts a new page load.
        """
        try:
            is_top_level = frame.parent_frame is None
        except Exception:
            is_top_level = True  # fail-closed: clear when uncertain
        if is_top_level:
            self._ssrf_host_cache.clear()

    async def _ssrf_route_guard(self, route: Any, request: Any) -> None:
        """Playwright route handler: abort requests to blocked/internal hosts.

        Runs on *every* request the page issues (navigation, redirects, and
        sub-resource loads alike) — a page can smuggle an SSRF probe through
        a same-origin ``<img>``/fetch as easily as through a top-level
        navigation. DNS resolution runs in a worker thread so it never blocks
        the event loop, and its verdict is cached per host for the lifetime
        of the current page load (see ``self._ssrf_host_cache`` and
        :meth:`_reset_ssrf_host_cache_on_navigation`) so a page with many
        same-host assets doesn't pay a DNS lookup per request. Fails closed
        on any error.
        """
        try:
            host = (urlparse(request.url).hostname or "").lower()
        except Exception:
            await route.abort("blockedbyclient")
            return
        verdict = self._ssrf_host_cache.get(host)
        if verdict is None:
            try:
                verdict = await asyncio.to_thread(
                    _url_is_blocked, request.url, resolve_dns=True
                )
            except Exception:
                verdict = True  # fail-closed
            self._ssrf_host_cache.set(host, verdict)
        if verdict:
            logger.warning("browser_ssrf_blocked_request", url=request.url)
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

            return _build_action(action_type, result)
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
