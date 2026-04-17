"""BaselithbotAgent — OpenClaw-style cognitive layer over BrowserAgent backend.

Implements an explicit Observe -> Plan -> Act loop:
    1. **Observe**: capture screenshot + DOM snippet via underlying BrowserAgent.
    2. **Plan**: VisionService analyzes screenshot, returns next BrowserAction
       in JSON form.
    3. **Act**: dispatch to a sanitized tool (Playwright primitive or
       whitelisted JS snippet).

Composes ``plugins.browser_agent.agent.BrowserAgent`` as the Playwright
backend; adds stealth, sanitized JS execution, lifecycle compliance, and
structured result envelopes.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.lifecycle.mixins import LifecycleMixin
from core.lifecycle.protocols import AgentState
from core.observability.logging import get_logger

from plugins.browser_agent.agent import BrowserAgent
from plugins.browser_agent.types import BrowserAction, BrowserActionType

from .stealth import apply_stealth, pick_user_agent
from .types import BaselithbotResult, BaselithbotTask, StealthConfig

logger = get_logger(__name__)


class BaselithbotAgent(LifecycleMixin):
    """Autonomous web-navigation agent with stealth and sanitized JS.

    Lifecycle:
        - ``startup()`` -> launches Playwright via ``BrowserAgent.start()``,
          applies stealth, transitions to ``AgentState.READY``.
        - ``execute(task, context)`` -> runs Observe-Plan-Act loop until
          DONE / FAIL / max_steps.
        - ``shutdown()`` -> gracefully stops the underlying browser.
    """

    def __init__(
        self,
        agent_id: str = "baselithbot",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.agent_id = agent_id
        cfg = config or {}

        self.headless: bool = cfg.get("headless", True)
        self.max_steps: int = cfg.get("max_steps", 20)
        self.viewport_width: int = cfg.get("viewport_width", 1280)
        self.viewport_height: int = cfg.get("viewport_height", 720)

        stealth_cfg = cfg.get("stealth", {})
        self.stealth_config: StealthConfig = (
            stealth_cfg
            if isinstance(stealth_cfg, StealthConfig)
            else StealthConfig(**stealth_cfg)
        )

        self._backend: BrowserAgent | None = None

    @property
    def backend(self) -> BrowserAgent:
        """Return the underlying BrowserAgent or raise if not started."""
        if self._backend is None:
            raise RuntimeError("Baselithbot backend not initialized; call startup() first")
        return self._backend

    async def _do_startup(self) -> None:
        """Launch Playwright backend and apply stealth countermeasures."""
        self._backend = BrowserAgent(
            headless=self.headless,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            max_steps=self.max_steps,
        )
        await self._backend.start()

        if self.stealth_config.enabled:
            user_agent = pick_user_agent(self.stealth_config)
            try:
                await self._backend._context.set_extra_http_headers(  # type: ignore[union-attr]
                    {"User-Agent": user_agent}
                )
            except Exception as exc:
                logger.warning("baselithbot_user_agent_set_failed", error=str(exc))

            await apply_stealth(self._backend._context, self.stealth_config)

        logger.info(
            "baselithbot_started",
            agent_id=self.agent_id,
            headless=self.headless,
            stealth=self.stealth_config.enabled,
        )

    async def _do_shutdown(self) -> None:
        """Stop the underlying Playwright backend."""
        if self._backend is not None:
            await self._backend.stop()
            self._backend = None
        logger.info("baselithbot_stopped", agent_id=self.agent_id)

    async def _do_health_check(self) -> dict[str, Any] | None:
        """Surface backend status in health checks."""
        return {
            "backend_started": self._backend is not None,
            "stealth_enabled": self.stealth_config.enabled,
        }

    async def execute(
        self,
        input: Any,
        context: dict[str, Any] | None = None,
    ) -> BaselithbotResult:
        """Run a Baselithbot task end-to-end.

        Args:
            input: Either a ``BaselithbotTask``, a dict matching its schema,
                or a plain string (treated as ``goal``).
            context: Optional execution context (unused for now).

        Returns:
            ``BaselithbotResult`` with success flag, final URL, and history.
        """
        if self.state != AgentState.READY:
            return BaselithbotResult(
                success=False,
                final_url="",
                steps_taken=0,
                error=f"Agent not ready (state={self.state.value})",
            )

        task = self._coerce_task(input)
        backend = self.backend

        if task.start_url:
            await backend.navigate(task.start_url)

        history: list[str] = []
        last_screenshot: str | None = None
        extracted: dict[str, Any] = {field: None for field in task.extract_fields}
        steps = 0

        try:
            while steps < task.max_steps:
                steps += 1
                state = await backend.get_page_state()
                last_screenshot = state.screenshot_base64

                action = await backend.decide_next_action(task.goal, state, history)
                history.append(f"{action.action_type.value}: {action.reasoning}")

                logger.info(
                    "baselithbot_step",
                    step=steps,
                    action=action.action_type.value,
                    url=state.url,
                )

                if action.action_type == BrowserActionType.DONE:
                    return BaselithbotResult(
                        success=True,
                        final_url=state.url,
                        steps_taken=steps,
                        extracted_data=extracted,
                        history=history,
                        last_screenshot_b64=last_screenshot,
                    )
                if action.action_type == BrowserActionType.FAIL:
                    return BaselithbotResult(
                        success=False,
                        final_url=state.url,
                        steps_taken=steps,
                        extracted_data=extracted,
                        history=history,
                        error=action.reasoning,
                        last_screenshot_b64=last_screenshot,
                    )
                if action.action_type == BrowserActionType.EXTRACT:
                    self._record_extraction(action, state.url, extracted)
                    continue

                ok = await backend.execute_action(action)
                if not ok:
                    await asyncio.sleep(0.5)

                await asyncio.sleep(0.3)

            final_url = backend._page.url if backend._page else ""  # type: ignore[union-attr]
            return BaselithbotResult(
                success=False,
                final_url=final_url,
                steps_taken=steps,
                extracted_data=extracted,
                history=history,
                error=f"Max steps ({task.max_steps}) reached",
                last_screenshot_b64=last_screenshot,
            )
        except Exception as exc:
            logger.exception("baselithbot_task_error", error=str(exc))
            final_url = backend._page.url if backend._page else ""  # type: ignore[union-attr]
            return BaselithbotResult(
                success=False,
                final_url=final_url,
                steps_taken=steps,
                extracted_data=extracted,
                history=history,
                error=str(exc),
                last_screenshot_b64=last_screenshot,
            )

    def _coerce_task(self, input: Any) -> BaselithbotTask:
        """Normalize ``execute`` input into a ``BaselithbotTask``."""
        if isinstance(input, BaselithbotTask):
            return input
        if isinstance(input, dict):
            return BaselithbotTask(**input)
        if isinstance(input, str):
            return BaselithbotTask(goal=input, max_steps=self.max_steps)
        raise TypeError(f"Unsupported input type for Baselithbot: {type(input)!r}")

    @staticmethod
    def _record_extraction(
        action: BrowserAction, url: str, store: dict[str, Any]
    ) -> None:
        """Persist EXTRACT action results into the store."""
        if not action.value:
            return
        for raw_field in action.value.split(","):
            field = raw_field.strip()
            if not field:
                continue
            store[field] = f"[extracted from {url}]"


__all__ = ["BaselithbotAgent"]
