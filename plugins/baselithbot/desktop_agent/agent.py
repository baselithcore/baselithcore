"""DesktopAgent — Observe -> Plan -> Act loop body."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from core.observability.logging import get_logger
from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionRequest,
)
from core.services.vision.service import VisionService

from ..computer_use import ComputerUseConfig
from .decision import (
    APP_LAUNCH_PAUSE_SECONDS,
    HISTORY_CONTEXT_WINDOW,
    LOOP_TOLERANT_TOOLS,
    OBSERVATION_SKIP_TOOLS,
    POST_INPUT_PAUSE_SECONDS,
    POST_INPUT_PAUSE_TOOLS,
    VISION_STEP_TIMEOUT_SECONDS,
    is_app_launch,
    parse_decision,
    summarize_result,
)
from .models import DesktopStep, DesktopTaskResult, ProgressCallback
from .policy import (
    EMPTY_POLICY_REASON,
    filter_tools_by_policy,
    format_tool_catalog,
)
from .prompt import SYSTEM_PROMPT_HEAD

logger = get_logger(__name__)


class DesktopAgent:
    """Observe -> Plan -> Act loop over the Computer Use tool surface."""

    def __init__(
        self,
        *,
        vision: VisionService,
        tools: dict[str, dict[str, Any]],
        policy: ComputerUseConfig,
    ) -> None:
        self._vision = vision
        self._tools = tools
        self._policy = policy
        self._allowed_tools = filter_tools_by_policy(tools, policy)
        # Tool catalog is invariant for the lifetime of this agent (tools and
        # policy are frozen at construction). Render once to avoid rebuilding
        # the same string on every ``_decide`` call in the Observe->Plan->Act
        # loop.
        self._tool_catalog = format_tool_catalog(tools, self._allowed_tools)
        # Usage accounting for the overview / cost report. Incremented on every
        # successful vision decision so each run can surface tokens + provider
        # on its DesktopTaskResult.
        self._vision_tokens_total: int = 0
        self._last_vision_model: str | None = None
        self._last_vision_provider: str | None = None

    @property
    def allowed_tools(self) -> list[str]:
        """Tool names the agent may invoke under the current policy."""
        return list(self._allowed_tools)

    async def execute(
        self,
        *,
        goal: str,
        max_steps: int = 15,
        on_progress: ProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> DesktopTaskResult:
        """Run the Observe -> Plan -> Act loop until DONE / FAIL / max_steps.

        Pass ``cancel_event`` to let an operator interrupt the run between
        steps (checked at the top of every iteration and after the decision
        call, so a tool already dispatched will finish before the agent
        stops).
        """
        tokens_before = self._vision_tokens_total
        result = await self._execute_inner(
            goal=goal,
            max_steps=max_steps,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        result.tokens_used = max(0, self._vision_tokens_total - tokens_before)
        result.model = self._last_vision_model
        result.provider = self._last_vision_provider
        return result

    async def _execute_inner(
        self,
        *,
        goal: str,
        max_steps: int,
        on_progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
    ) -> DesktopTaskResult:
        if not self._allowed_tools:
            return DesktopTaskResult(
                success=False,
                steps_taken=0,
                goal=goal,
                error=EMPTY_POLICY_REASON,
            )

        history: list[DesktopStep] = []
        last_screenshot: str | None = None
        final_reasoning = ""

        def _cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        for step_idx in range(1, max_steps + 1):
            if _cancelled():
                return DesktopTaskResult(
                    success=False,
                    steps_taken=step_idx - 1,
                    goal=goal,
                    history=history,
                    final_reasoning="cancelled by operator",
                    error="cancelled by operator",
                    last_screenshot_b64=last_screenshot,
                )
            if self._should_skip_observe(history):
                # Reuse the cached screenshot — the previous tool did not
                # change the visible desktop (shell stdout / spotify API /
                # sandboxed fs). Saves one mss.grab + JPEG encode + base64
                # + vision-token cost per skippable iteration.
                screenshot_b64 = last_screenshot
            else:
                screenshot_b64 = await self._observe()
                if screenshot_b64 is not None:
                    last_screenshot = screenshot_b64

            decision = await self._decide(
                goal=goal,
                history=history,
                screenshot_b64=screenshot_b64,
            )
            tool_name = str(decision.get("tool", "")).strip()
            reasoning = str(decision.get("reasoning", "")).strip()

            if tool_name == "done":
                final_reasoning = reasoning or "agent reported done"
                return DesktopTaskResult(
                    success=True,
                    steps_taken=step_idx - 1,
                    goal=goal,
                    history=history,
                    final_reasoning=final_reasoning,
                    last_screenshot_b64=last_screenshot,
                )
            if tool_name == "fail":
                final_reasoning = reasoning or "agent reported fail"
                return DesktopTaskResult(
                    success=False,
                    steps_taken=step_idx - 1,
                    goal=goal,
                    history=history,
                    final_reasoning=final_reasoning,
                    error=final_reasoning,
                    last_screenshot_b64=last_screenshot,
                )

            repeat_threshold = 3 if tool_name in LOOP_TOLERANT_TOOLS else 2
            candidate_args = decision.get("args") or {}
            window = history[-repeat_threshold:]
            if len(window) >= repeat_threshold and all(
                prior.tool == tool_name and prior.args == candidate_args for prior in window
            ):
                final_reasoning = f"agent looped on {tool_name} with identical args; aborting"
                return DesktopTaskResult(
                    success=False,
                    steps_taken=step_idx - 1,
                    goal=goal,
                    history=history,
                    final_reasoning=final_reasoning,
                    error=final_reasoning,
                    last_screenshot_b64=last_screenshot,
                )

            if tool_name not in self._allowed_tools:
                step = DesktopStep(
                    step=step_idx,
                    tool=tool_name or "(missing)",
                    args=decision.get("args", {}) or {},
                    reasoning=reasoning,
                    status="denied",
                    result_summary=(
                        f"tool {tool_name!r} is not available under the current policy"
                    ),
                )
                history.append(step)
                await _emit(on_progress, step, last_screenshot, goal)
                continue

            raw_args = decision.get("args", {}) or {}
            args = raw_args if isinstance(raw_args, dict) else {}
            if _cancelled():
                return DesktopTaskResult(
                    success=False,
                    steps_taken=step_idx - 1,
                    goal=goal,
                    history=history,
                    final_reasoning="cancelled by operator",
                    error="cancelled by operator",
                    last_screenshot_b64=last_screenshot,
                )
            handler = self._tools[tool_name]["handler"]
            try:
                result = await handler(**args)
            except TypeError as exc:
                status = "error"
                result = {"status": status, "error": f"invalid args: {exc}"}
            except Exception as exc:  # pragma: no cover - defensive path
                logger.exception("desktop_agent_tool_error", tool=tool_name)
                status = "error"
                result = {"status": status, "error": str(exc)}

            status = str(result.get("status", "success"))
            summary = summarize_result(result if isinstance(result, dict) else {})

            if tool_name == "baselithbot_desktop_screenshot" and status == "success":
                last_screenshot = str(result.get("screenshot_base64", last_screenshot or ""))

            step = DesktopStep(
                step=step_idx,
                tool=tool_name,
                args=args,
                reasoning=reasoning,
                status=status,
                result_summary=summary,
            )
            history.append(step)
            await _emit(on_progress, step, last_screenshot, goal)

            if is_app_launch(tool_name, args) and status == "success":
                await asyncio.sleep(APP_LAUNCH_PAUSE_SECONDS)
            elif tool_name in POST_INPUT_PAUSE_TOOLS and status == "success":
                await asyncio.sleep(POST_INPUT_PAUSE_SECONDS)

            if status == "error":
                return DesktopTaskResult(
                    success=False,
                    steps_taken=step_idx,
                    goal=goal,
                    history=history,
                    final_reasoning=reasoning,
                    error=summary,
                    last_screenshot_b64=last_screenshot,
                )

        return DesktopTaskResult(
            success=False,
            steps_taken=max_steps,
            goal=goal,
            history=history,
            final_reasoning=final_reasoning,
            error="max_steps reached without reaching done/fail",
            last_screenshot_b64=last_screenshot,
        )

    @staticmethod
    def _should_skip_observe(history: list[DesktopStep]) -> bool:
        """Return ``True`` when the previous tool did not affect the desktop.

        Shell stdout, Spotify's AppleScript bridge, and sandboxed filesystem
        operations do not change the visible layout, so the cached screenshot
        from the prior Observe is still fresh. Shell runs that launched a GUI
        app (``open -a ...``) are intentionally NOT skipped — the new window
        must be captured. Non-success prior steps also re-observe so the model
        can see whatever error state is now on screen.
        """
        if not history:
            return False
        prev = history[-1]
        if prev.status != "success":
            return False
        if prev.tool in OBSERVATION_SKIP_TOOLS:
            return True
        if prev.tool == "baselithbot_shell_run" and not is_app_launch(prev.tool, prev.args):
            return True
        return False

    async def _observe(self) -> str | None:
        """Best-effort screenshot. Returns ``None`` if screenshots are blocked."""
        if "baselithbot_desktop_screenshot" not in self._allowed_tools:
            return None
        spec = self._tools.get("baselithbot_desktop_screenshot")
        if spec is None:
            return None
        try:
            result = await spec["handler"](monitor=1, image_format="JPEG", quality=65)
        except Exception:  # pragma: no cover - defensive
            logger.exception("desktop_agent_observe_failed")
            return None
        if not isinstance(result, dict) or result.get("status") != "success":
            return None
        b64 = result.get("screenshot_base64")
        return str(b64) if isinstance(b64, str) and b64 else None

    async def _decide(
        self,
        *,
        goal: str,
        history: list[DesktopStep],
        screenshot_b64: str | None,
    ) -> dict[str, Any]:
        """Call the vision model and return a parsed decision dict."""
        window = history[-HISTORY_CONTEXT_WINDOW:]
        dropped = max(0, len(history) - len(window))
        lines: list[str] = []
        if dropped > 0:
            lines.append(f"(+{dropped} earlier actions elided for context budget)")
        lines.extend(
            f"#{s.step} {s.tool}({s.args_json}) -> {s.status}: {s.result_summary[:100]}"
            for s in window
        )
        history_text = "\n".join(lines) if lines else "(no previous actions)"

        prompt = (
            f"{SYSTEM_PROMPT_HEAD}\n\n"
            f"TOOLS:\n{self._tool_catalog}\n\n"
            f"GOAL:\n{goal}\n\n"
            f"RECENT ACTIONS:\n{history_text}\n\n"
            "Emit the next JSON action now."
        )
        request = VisionRequest(
            prompt=prompt,
            images=[ImageContent.from_base64(screenshot_b64)] if screenshot_b64 else [],
            capability=VisionCapability.SCREENSHOT_ANALYSIS,
            json_mode=True,
            max_tokens=400,
            temperature=0.0,
        )
        try:
            response = await asyncio.wait_for(
                self._vision.analyze(request),
                timeout=VISION_STEP_TIMEOUT_SECONDS,
            )
            self._vision_tokens_total += int(getattr(response, "tokens_used", 0) or 0)
            self._last_vision_model = getattr(response, "model", None) or self._last_vision_model
            self._last_vision_provider = (
                getattr(response, "provider", None) or self._last_vision_provider
            )
        except asyncio.TimeoutError:
            logger.warning(
                "desktop_agent_vision_timeout",
                seconds=VISION_STEP_TIMEOUT_SECONDS,
            )
            return {
                "tool": "fail",
                "reasoning": (
                    f"vision model exceeded {VISION_STEP_TIMEOUT_SECONDS:.0f}s idle "
                    "budget on a single decision"
                ),
            }
        decision = None
        if isinstance(response.raw_response, dict):
            decision = response.as_json
        if decision is None:
            decision = parse_decision(response.content)
        if decision is None:
            logger.warning(
                "desktop_agent_non_json",
                content=response.content[:300],
                provider=response.provider,
                model=response.model,
            )
            return {
                "tool": "fail",
                "reasoning": (f"model did not return JSON; content: {response.content[:200]!r}"),
            }
        return decision


async def _emit(
    callback: ProgressCallback | None,
    step: DesktopStep,
    screenshot_b64: str | None,
    goal: str,
) -> None:
    """Invoke the progress callback if supplied (sync or async)."""
    if callback is None:
        return
    payload = {
        "goal": goal,
        "step": step.step,
        "tool": step.tool,
        "args": step.args,
        "reasoning": step.reasoning,
        "status": step.status,
        "result_summary": step.result_summary,
        "last_screenshot_b64": screenshot_b64,
    }
    outcome = callback(payload)
    if inspect.isawaitable(outcome):
        await outcome


__all__ = ["DesktopAgent"]
