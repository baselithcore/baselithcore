"""Desktop agent — natural-language loop over Computer Use tools.

Observe -> Plan -> Act loop that lets a user describe an OS-level goal in
plain language (``"apri spotify e riproduci preferiti"``) and dispatches
the right primitives from the Computer Use tool surface (desktop
screenshots, shell launcher, mouse, keyboard, filesystem).

The agent is deliberately tool-agnostic: it receives a ``tool_map``
dictionary built by ``BaselithbotPlugin.build_computer_tool_map()`` and
only advertises the subset that the effective ``ComputerUseConfig``
actually allows. Every decision is a single JSON object produced by the
vision model (screenshot + textual history + tool catalog) so the agent
stays provider-agnostic across Anthropic / OpenAI / Ollama / LlamaCPP.
"""

from __future__ import annotations

import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from core.observability.logging import get_logger
from core.services.vision.models import (
    ImageContent,
    VisionCapability,
    VisionRequest,
)
from core.services.vision.service import VisionService

from .computer_use import ComputerUseConfig

logger = get_logger(__name__)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class DesktopStep(BaseModel):
    """One Observe -> Plan -> Act iteration."""

    step: int
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""
    status: str = ""
    result_summary: str = ""
    ts: float = Field(default_factory=time.time)


class DesktopTaskResult(BaseModel):
    """Terminal outcome of a desktop agent run."""

    success: bool
    steps_taken: int
    goal: str
    history: list[DesktopStep] = Field(default_factory=list)
    final_reasoning: str = ""
    error: str | None = None
    last_screenshot_b64: str | None = None


_SYSTEM_PROMPT_HEAD = """You control a desktop computer on behalf of an operator.

You are driving an Observe -> Plan -> Act loop. At each step you receive:
  - The user goal
  - A screenshot of the current desktop (may be missing if screenshots are disabled)
  - The recent action history with each tool result status
  - The exact list of tools you are allowed to invoke

Respond with ONE JSON object and nothing else:

  {"tool": "<tool_name>", "args": {...}, "reasoning": "why this advances the goal"}

To finish the task with success:
  {"tool": "done", "reasoning": "goal achieved because ..."}

To abandon the task with an explanation:
  {"tool": "fail", "reasoning": "cannot proceed because ..."}

Hard rules:
  - Output MUST be a single JSON object, no prose, no markdown fences.
  - Only call tools listed below. Never invent arguments that are not in the schema.
  - On macOS prefer shell_run with `open -a <App>` to launch GUI applications.
  - Prefer keyboard shortcuts over precise mouse clicks when possible (they survive layout changes).
  - Stop as soon as the observable desktop state matches the goal.
  - If a previous step returned status "denied" or "error", do NOT repeat the same call with the same args."""


_EMPTY_POLICY_REASON = (
    "No Computer Use capabilities are available. Enable the master switch and "
    "the required capabilities on the Computer Use page, then try again."
)


def _summarize_result(result: dict[str, Any]) -> str:
    """One-line human summary of a tool result for history context."""
    status = str(result.get("status", ""))
    if isinstance(result.get("error"), str) and result["error"].strip():
        return f"{status}: {result['error'][:120]}"
    if isinstance(result.get("stdout"), str) and result["stdout"].strip():
        return f"{status}: {result['stdout'].strip()[:120]}"
    if isinstance(result.get("stderr"), str) and result["stderr"].strip():
        return f"{status}: {result['stderr'].strip()[:120]}"
    if "return_code" in result:
        return f"{status} rc={result['return_code']}"
    if "entries" in result and isinstance(result["entries"], list):
        return f"{status}: {len(result['entries'])} entries"
    if status == "success":
        return "success"
    return status or "unknown"


def _format_tool_catalog(
    tools: dict[str, dict[str, Any]], allowed_names: list[str]
) -> str:
    """Render tool docs for the system prompt (name + description + schema)."""
    lines: list[str] = []
    for name in allowed_names:
        spec = tools.get(name)
        if spec is None:
            continue
        description = spec.get("description", "")
        schema = json.dumps(spec.get("input_schema", {}), separators=(",", ":"))
        lines.append(f"- {name}: {description}\n  schema: {schema}")
    return "\n".join(lines) if lines else "(none)"


def _filter_tools_by_policy(
    tools: dict[str, dict[str, Any]], policy: ComputerUseConfig
) -> list[str]:
    """Return the tool names that are actually reachable under ``policy``."""
    if not policy.enabled:
        return []
    allowed: list[str] = []
    mapping = {
        "allow_screenshot": [
            "baselithbot_desktop_screenshot",
            "baselithbot_screen_size",
        ],
        "allow_mouse": [
            "baselithbot_mouse_move",
            "baselithbot_mouse_click",
            "baselithbot_mouse_scroll",
        ],
        "allow_keyboard": [
            "baselithbot_kbd_type",
            "baselithbot_kbd_press",
            "baselithbot_kbd_hotkey",
        ],
        "allow_shell": ["baselithbot_shell_run"],
        "allow_filesystem": [
            "baselithbot_fs_read",
            "baselithbot_fs_write",
            "baselithbot_fs_list",
        ],
    }
    for attr, names in mapping.items():
        if getattr(policy, attr, False):
            allowed.extend(n for n in names if n in tools)
    return allowed


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def _parse_decision(raw_content: str) -> dict[str, Any] | None:
    """Parse the LLM response into a decision object. Returns ``None`` on failure."""
    if not raw_content:
        return None
    stripped = raw_content.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(stripped)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


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
        self._allowed_tools = _filter_tools_by_policy(tools, policy)

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
    ) -> DesktopTaskResult:
        """Run the Observe -> Plan -> Act loop until DONE / FAIL / max_steps."""
        if not self._allowed_tools:
            return DesktopTaskResult(
                success=False,
                steps_taken=0,
                goal=goal,
                error=_EMPTY_POLICY_REASON,
            )

        history: list[DesktopStep] = []
        last_screenshot: str | None = None
        final_reasoning = ""

        for step_idx in range(1, max_steps + 1):
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
            summary = _summarize_result(result if isinstance(result, dict) else {})

            if tool_name == "baselithbot_desktop_screenshot" and status == "success":
                last_screenshot = str(
                    result.get("screenshot_base64", last_screenshot or "")
                )

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

    async def _observe(self) -> str | None:
        """Best-effort screenshot. Returns ``None`` if screenshots are blocked."""
        if "baselithbot_desktop_screenshot" not in self._allowed_tools:
            return None
        spec = self._tools.get("baselithbot_desktop_screenshot")
        if spec is None:
            return None
        try:
            result = await spec["handler"](
                monitor=1, image_format="JPEG", quality=65
            )
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
        catalog = _format_tool_catalog(self._tools, self._allowed_tools)
        history_text = (
            "\n".join(
                f"#{s.step} {s.tool}({json.dumps(s.args, separators=(',', ':'))}) "
                f"-> {s.status}: {s.result_summary[:100]}"
                for s in history[-8:]
            )
            or "(no previous actions)"
        )

        prompt = (
            f"{_SYSTEM_PROMPT_HEAD}\n\n"
            f"TOOLS:\n{catalog}\n\n"
            f"GOAL:\n{goal}\n\n"
            f"RECENT ACTIONS:\n{history_text}\n\n"
            "Emit the next JSON action now."
        )
        request = VisionRequest(
            prompt=prompt,
            images=[ImageContent.from_base64(screenshot_b64)]
            if screenshot_b64
            else [],
            capability=VisionCapability.SCREENSHOT_ANALYSIS,
            json_mode=True,
            max_tokens=400,
            temperature=0.0,
        )
        response = await self._vision.analyze(request)
        decision = None
        if isinstance(response.raw_response, dict):
            decision = response.as_json
        if decision is None:
            decision = _parse_decision(response.content)
        if decision is None:
            logger.warning(
                "desktop_agent_non_json",
                content=response.content[:300],
                provider=response.provider,
                model=response.model,
            )
            return {
                "tool": "fail",
                "reasoning": (
                    "model did not return JSON; content: "
                    f"{response.content[:200]!r}"
                ),
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


__all__ = [
    "DesktopAgent",
    "DesktopStep",
    "DesktopTaskResult",
]
