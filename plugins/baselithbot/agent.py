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
import inspect
from typing import Any

from core.lifecycle.mixins import LifecycleMixin
from core.lifecycle.protocols import AgentState
from core.observability.logging import get_logger
from core.services.vision.service import VisionService
from plugins.browser_agent.agent import BrowserAgent
from plugins.browser_agent.types import BrowserAction, BrowserActionType

from .stealth import apply_stealth, build_browser_context_options
from .types import BaselithbotResult, BaselithbotTask, StealthConfig

logger = get_logger(__name__)


def _hashable(value: Any) -> Any:
    """Coerce ``value`` into something hashable for de-duplication sets.

    Returns ``None`` for values that cannot be meaningfully hashed (the
    caller treats ``None`` as "cannot dedupe — keep the item as-is").
    """
    try:
        hash(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            try:
                return tuple(sorted(value.items()))
            except TypeError:
                return repr(value)
        if isinstance(value, list):
            return tuple(_hashable(item) for item in value)
        return repr(value)


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
            stealth_cfg if isinstance(stealth_cfg, StealthConfig) else StealthConfig(**stealth_cfg)
        )

        vision_service = cfg.get("vision_service")
        self._vision_service: VisionService | None = (
            vision_service if isinstance(vision_service, VisionService) else None
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
            vision_service=self._vision_service,
            context_options=build_browser_context_options(self.stealth_config),
        )
        await self._backend.start()

        if self.stealth_config.enabled:
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

        def _safe_int(value: Any) -> int:
            return value if isinstance(value, int) else 0

        def _safe_str(value: Any) -> str | None:
            return value if isinstance(value, str) else None

        tokens_before = (
            _safe_int(getattr(self._backend, "_vision_tokens_total", 0))
            if self._backend is not None
            else 0
        )
        result = await self._execute_inner(input, context)
        if self._backend is not None:
            tokens_after = _safe_int(getattr(self._backend, "_vision_tokens_total", 0))
            result.tokens_used = max(0, tokens_after - tokens_before)
            result.model = _safe_str(getattr(self._backend, "_last_vision_model", None))
            result.provider = _safe_str(getattr(self._backend, "_last_vision_provider", None))
        return result

    async def _execute_inner(
        self,
        input: Any,
        context: dict[str, Any] | None = None,
    ) -> BaselithbotResult:
        if self.state != AgentState.READY:
            return BaselithbotResult(
                success=False,
                final_url="",
                steps_taken=0,
                error=f"Agent not ready (state={self.state.value})",
            )

        task = self._coerce_task(input)
        backend = self.backend
        callback = (context or {}).get("on_progress")

        async def emit_progress(
            *,
            current_url: str,
            action: BrowserAction,
            steps_taken: int,
            history: list[str],
            extracted_data: dict[str, Any],
            screenshot_b64: str | None,
        ) -> None:
            if not callable(callback):
                return
            payload = {
                "steps_taken": steps_taken,
                "current_url": current_url,
                "action": action.action_type.value,
                "reasoning": action.reasoning,
                "history": list(history),
                "extracted_data": dict(extracted_data),
                "last_screenshot_b64": screenshot_b64,
            }
            maybe_awaitable = callback(payload)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

        if task.start_url:
            await backend.navigate(task.start_url)

        history: list[str] = []
        last_screenshot: str | None = None
        extracted: dict[str, Any] = {field: None for field in task.extract_fields}
        steps = 0
        no_progress_streak = 0

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
                    before = self._extraction_signature(extracted)
                    self._record_extraction(action, state.url, extracted)
                    await emit_progress(
                        current_url=state.url,
                        action=action,
                        steps_taken=steps,
                        history=history,
                        extracted_data=extracted,
                        screenshot_b64=last_screenshot,
                    )
                    all_fields_filled = bool(task.extract_fields) and all(
                        extracted.get(f) for f in task.extract_fields
                    )
                    if all_fields_filled:
                        return BaselithbotResult(
                            success=True,
                            final_url=state.url,
                            steps_taken=steps,
                            extracted_data=extracted,
                            history=history,
                            last_screenshot_b64=last_screenshot,
                        )
                    after = self._extraction_signature(extracted)
                    if before == after:
                        no_progress_streak += 1
                    else:
                        no_progress_streak = 0
                    if no_progress_streak >= 3:
                        return BaselithbotResult(
                            success=True,
                            final_url=state.url,
                            steps_taken=steps,
                            extracted_data=extracted,
                            history=history,
                            last_screenshot_b64=last_screenshot,
                        )
                    # Auto-scroll to reveal more content so the next
                    # extract sees fresh items instead of the same viewport.
                    # Models often ignore the "scroll between extracts"
                    # hint, so we enforce it here.
                    scroll_action = BrowserAction(
                        action_type=BrowserActionType.SCROLL,
                        value="down",
                        reasoning="auto-scroll after extract to reveal more items",
                    )
                    await backend.execute_action(scroll_action)
                    history.append("scroll: auto-scroll after extract")
                    await asyncio.sleep(0.5)
                    continue

                ok = await backend.execute_action(action)
                current_url = backend._page.url if backend._page else state.url  # type: ignore[union-attr]
                await emit_progress(
                    current_url=current_url,
                    action=action,
                    steps_taken=steps,
                    history=history,
                    extracted_data=extracted,
                    screenshot_b64=last_screenshot,
                )
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
    def _record_extraction(action: BrowserAction, url: str, store: dict[str, Any]) -> None:
        """Merge EXTRACT action results into the store.

        Accepts two shapes from the vision model:

        1. Structured — ``action.data`` is a dict of ``{field: value}``.
           List-valued fields accumulate across steps with duplicate
           suppression, so scroll+extract loops grow the collection
           instead of overwriting it. Scalar fields overwrite on each
           extract.
        2. Legacy — ``action.value`` is a comma-separated list of field
           names; kept only as a fallback marker so older models still
           produce *some* output.
        """
        if action.data:
            for raw_field, value in action.data.items():
                field = str(raw_field)
                if isinstance(value, list):
                    existing = store.get(field)
                    merged: list[Any] = list(existing) if isinstance(existing, list) else []
                    seen = {_hashable(item) for item in merged if _hashable(item) is not None}
                    for item in value:
                        key = _hashable(item)
                        if key is None or key in seen:
                            continue
                        seen.add(key)
                        merged.append(item)
                    store[field] = merged
                else:
                    store[field] = value
            return
        if not action.value:
            return
        for raw_field in action.value.split(","):
            field = raw_field.strip()
            if not field:
                continue
            store[field] = f"[extracted from {url}]"

    @staticmethod
    def _extraction_signature(store: dict[str, Any]) -> tuple[Any, ...]:
        """Return a stable, hashable snapshot of the store for progress detection."""
        parts: list[tuple[str, int, Any]] = []
        for field in sorted(store.keys()):
            value = store[field]
            if isinstance(value, list):
                parts.append((field, len(value), _hashable(value[-1]) if value else None))
            else:
                parts.append((field, -1, _hashable(value)))
        return tuple(parts)


__all__ = ["BaselithbotAgent"]
