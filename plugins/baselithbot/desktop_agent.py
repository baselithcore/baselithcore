"""Desktop agent — natural-language loop over Computer Use tools.

Observe -> Plan -> Act loop that lets a user describe an OS-level goal in
plain language (e.g. ``"open spotify and play my liked songs"``) and
dispatches the right primitives from the Computer Use tool surface
(desktop screenshots, shell launcher, mouse, keyboard, filesystem).

The agent is deliberately tool-agnostic: it receives a ``tool_map``
dictionary built by ``BaselithbotPlugin.build_computer_tool_map()`` and
only advertises the subset that the effective ``ComputerUseConfig``
actually allows. Every decision is a single JSON object produced by the
vision model (screenshot + textual history + tool catalog) so the agent
stays provider-agnostic across Anthropic / OpenAI / Ollama / LlamaCPP.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

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
    # Precomputed compact JSON of ``args``. Cached on first access so the
    # history context renderer in ``_decide`` does not re-serialise the same
    # dict on every subsequent agent iteration. ``PrivateAttr`` keeps it off
    # the default serialisation surface used by progress callbacks.
    _args_json_cache: str | None = PrivateAttr(default=None)

    @property
    def args_json(self) -> str:
        cached = self._args_json_cache
        if cached is None:
            cached = json.dumps(self.args, separators=(",", ":"))
            self._args_json_cache = cached
        return cached


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
  - Never invent values: use only text the user provided or text you can read on the screenshot.
  - On macOS ALWAYS launch GUI apps with baselithbot_shell_run and command "open -a <AppName>" first.
  - After shell_run launches an app, take ONE screenshot with baselithbot_desktop_screenshot before any click/type, so you can see where elements are.
  - NEVER emit "done" immediately after shell_run or after a single screenshot. Launching an app is NOT the goal — the goal is the final interaction (playlist playing, file saved, window focused on the right view, etc.).
  - Multi-step goals ("open X and do Y") require completing BOTH actions: launching alone is insufficient, you must also perform Y and observe it succeeded.
  - STRONGLY prefer keyboard shortcuts over mouse clicks. Reason: mouse coordinates on Retina / HiDPI macOS may mismatch between screenshot pixels and pyautogui logical pixels, so clicks often miss their target. Keyboard shortcuts always land correctly.
  - For macOS Spotify control, ALWAYS prefer the dedicated `baselithbot_spotify` tool over shell_run / mouse / keyboard. It is deterministic, needs no screenshot, and bypasses HiDPI coordinate issues. Actions available: play, pause, toggle, next, previous, play_uri (requires a `spotify:...` URI), status.
  - Spotify preference order:
      1. `baselithbot_spotify action=play` — resumes Spotify's last playback context (perfect when the user previously loaded the target playlist, including the user's Liked Songs collection).
      2. `baselithbot_spotify action=play_uri uri="spotify:..."` — exact target when the goal supplies a URI.
      3. `baselithbot_spotify action=status` — inspect what is playing when you need to confirm state without a screenshot.
      4. Keyboard fallback: search overlay (cmd+K) → type query → enter → space.
      5. Mouse click on the Play button is the LAST resort and must use coordinates from the most recent screenshot only.
  - For other macOS GUI apps with AppleScript support (Music, Finder, Mail, Calendar, Safari, iTerm2, System Events), you may use baselithbot_shell_run with `osascript -e '<applescript>'`; requires `osascript` in the shell allowlist.
  - When the goal explicitly asks to run a command IN the Terminal (visible to the user), do NOT just shell_run the command in the background — that captures output for the agent only, the user's Terminal stays empty. Instead use AppleScript to have Terminal execute + display it: `osascript -e 'tell application "Terminal" to do script "<COMMAND>"'`. This opens Terminal (if closed), creates a new window, runs the command, and leaves the output visible. Only fall back to plain shell_run when the goal is purely informational ("how much disk space" without "open the terminal").
  - For ANY web task (gmail, calendar, amazon, youtube, docs, search, news), use baselithbot_open_url with the full https URL. It launches the OS default browser on the exact page. Do NOT cmd+L + type + enter; do NOT try to read email/calendar/social-media content from a screenshot (vision cannot reliably parse dense web UIs). After opening, if the goal is "open <site>" you are DONE — the user will read the content themselves. If the goal is "read/summarize/reply" on a web service, emit fail with reasoning "task requires authenticated API access (e.g. Gmail API via OAuth); desktop agent cannot read web content reliably".
  - Never emit repeated keyboard refresh (cmd+R / f5) — if a page did not load, emit fail rather than retrying.
  - If a mouse_click did not produce the expected state change, do NOT retry the same coordinates; switch to a keyboard shortcut (space, enter, tab) or a different element.
  - baselithbot_fs_* tools are SANDBOXED to filesystem_root. Do NOT use them for system paths (/Applications, /Users, /Volumes, /System). They will be denied.
  - For any system introspection (disk usage, memory, running processes, uptime, uname, network), use baselithbot_shell_run with the appropriate UNIX tool — NEVER fs_list. Examples:
      - disk space / free space → `df -h` or `df -h /`
      - directory size → `du -sh <path>`
      - memory usage → `vm_stat` (macOS) / `free -h` (Linux)
      - running processes → `ps aux`
      - cpu/load → `uptime`, `top -l 1 -n 0` (macOS)
      - listing system dirs → `ls -la /Applications` (requires `ls` in allowlist)
    The relevant binary MUST be in the shell allowlist.
  - Do NOT repeat a tool call with the same args on consecutive steps.
  - If the previous step returned status "denied" or "error", change approach, do not retry.
  - Stopping criteria, by goal type:
      - INFORMATIONAL goals ("how much disk", "which processes", "what time", "show status"): emit done IMMEDIATELY after the single tool call that produced the answer. The tool result IS the answer — do NOT re-run the same command, do NOT take a screenshot to "confirm" text output. Include the key figure in the done reasoning.
      - ACTION goals ("open X", "play Y", "launch Z"): emit done after the action visibly/observably succeeded (screenshot or deterministic API status call like baselithbot_spotify action=status).
      - MULTI-STEP goals ("open X and do Y"): every clause must be completed before done.
  - A tool that returned status=success with the needed payload is a completed step. Repeating the same tool+args is FORBIDDEN.

Few-shot examples (follow this exact pattern):

GOAL: "open Finder"
  {"tool": "baselithbot_shell_run", "args": {"command": "open -a Finder"}, "reasoning": "launch Finder via macOS open"}
  {"tool": "done", "reasoning": "Finder window is visible in screenshot"}

GOAL: "how much free disk space do I have?"  (informational — one shell call, then done)
  {"tool": "baselithbot_shell_run", "args": {"command": "df -h /"}, "reasoning": "df reports human-readable disk usage for the root filesystem"}
  {"tool": "done", "reasoning": "root filesystem: <size> total, <avail> free, <pct> used — from df output"}

GOAL: "open the terminal and show me the disk usage"  (action — output must be visible IN Terminal)
  {"tool": "baselithbot_shell_run", "args": {"command": "osascript -e 'tell application \"Terminal\" to do script \"df -h\"'"}, "reasoning": "Terminal.app 'do script' opens a visible window and runs df in it so the user can read the output"}
  {"tool": "done", "reasoning": "Terminal is now open with df output visible to the user"}

GOAL: "how many processes are running?"  (informational — one shell call, then done)
  {"tool": "baselithbot_shell_run", "args": {"command": "ps ax | wc -l"}, "reasoning": "ps | wc gives a single integer count"}
  {"tool": "done", "reasoning": "running process count = <N> (from ps | wc -l output)"}

GOAL: "open gmail"
  {"tool": "baselithbot_open_url", "args": {"url": "https://mail.google.com"}, "reasoning": "deterministic URL open bypasses address-bar automation"}
  {"tool": "done", "reasoning": "Gmail is loading in the default browser; the user will read it themselves"}

GOAL: "read my gmail messages"
  {"tool": "fail", "reasoning": "reading Gmail content reliably requires authenticated API access (Gmail REST API via OAuth2); desktop vision cannot parse threaded mail UIs. Use a Gmail MCP server or the plugin's Gmail Pub/Sub integration instead."}

GOAL: "open Spotify and play my liked songs"
  {"tool": "baselithbot_shell_run", "args": {"command": "open -a Spotify"}, "reasoning": "launch Spotify"}
  {"tool": "baselithbot_spotify", "args": {"action": "play"}, "reasoning": "resume the last-loaded context (usually the user's liked songs collection)"}
  {"tool": "baselithbot_spotify", "args": {"action": "status"}, "reasoning": "verify playback without a screenshot"}
  {"tool": "done", "reasoning": "Spotify player_state reports 'playing'"}

GOAL: "open Spotify and start the playlist <name>"
  {"tool": "baselithbot_shell_run", "args": {"command": "open -a Spotify"}, "reasoning": "launch Spotify"}
  {"tool": "baselithbot_desktop_screenshot", "args": {"monitor": 1}, "reasoning": "observe Spotify UI"}
  {"tool": "baselithbot_kbd_hotkey", "args": {"keys": ["cmd", "k"]}, "reasoning": "Spotify search shortcut"}
  {"tool": "baselithbot_kbd_type", "args": {"text": "<name>"}, "reasoning": "type playlist name"}
  {"tool": "baselithbot_kbd_press", "args": {"key": "enter"}, "reasoning": "open first result"}
  {"tool": "baselithbot_kbd_press", "args": {"key": "space"}, "reasoning": "toggle playback (NEVER mouse_click on Play)"}
  {"tool": "baselithbot_desktop_screenshot", "args": {"monitor": 1}, "reasoning": "confirm now-playing bar visible"}
  {"tool": "done", "reasoning": "playlist <name> is now playing"}"""


_EMPTY_POLICY_REASON = (
    "No Computer Use capabilities are available. Enable the master switch and "
    "the required capabilities on the Computer Use page, then try again."
)

_APP_LAUNCH_PAUSE_SECONDS = 2.5
_POST_INPUT_PAUSE_SECONDS = 0.4
# Hard ceiling per vision decision call — bounded idle time protects the
# agent from a hung model (mirrors OpenClaw's per-step LLM idle timeout).
_VISION_STEP_TIMEOUT_SECONDS = 90.0
# How many recent steps of context we surface to the model each turn.
# Older actions are summarised / dropped so the prompt stays bounded.
_HISTORY_CONTEXT_WINDOW = 6
_POST_INPUT_PAUSE_TOOLS = {
    "baselithbot_kbd_type",
    "baselithbot_kbd_press",
    "baselithbot_kbd_hotkey",
    "baselithbot_mouse_click",
}
# Tools that MUST stop after 2 consecutive identical calls — non-idempotent
# and cheap to check against the loop guard.
_LOOP_TOLERANT_TOOLS = {
    "baselithbot_kbd_press",
    "baselithbot_kbd_hotkey",
    "baselithbot_desktop_screenshot",
    "baselithbot_mouse_scroll",
}
# Tools whose result fully answers the model (stdout, deterministic API
# response, sandboxed filesystem ops) so observing a fresh screenshot before
# the next decision is redundant. When the previous step invoked one of these
# the agent reuses the cached ``last_screenshot`` instead of taking a new
# capture, saving ~mss.grab + JPEG encode + base64 + vision-token cost every
# skippable iteration. ``baselithbot_shell_run`` is conditional: we still
# re-observe after GUI app launches (``open -a ...``) because the screen
# layout has changed in a way the model needs to see.
_OBSERVATION_SKIP_TOOLS = {
    "baselithbot_spotify",
    "baselithbot_open_url",
    "baselithbot_fs_read",
    "baselithbot_fs_write",
    "baselithbot_fs_list",
    "baselithbot_screen_size",
}
_APP_LAUNCH_RE = re.compile(r"^\s*open\s+-a\s+", re.IGNORECASE)


def _is_app_launch(tool_name: str, args: dict[str, Any]) -> bool:
    """Detect ``shell_run`` invocations that boot a GUI app via ``open -a``."""
    if tool_name != "baselithbot_shell_run":
        return False
    cmd = args.get("command")
    if not isinstance(cmd, str):
        return False
    return bool(_APP_LAUNCH_RE.match(cmd))


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
        "allow_shell": [
            "baselithbot_shell_run",
            "baselithbot_spotify",
            "baselithbot_open_url",
        ],
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
        # Tool catalog is invariant for the lifetime of this agent (tools and
        # policy are frozen at construction). Render once to avoid rebuilding
        # the same string on every ``_decide`` call in the Observe->Plan->Act
        # loop.
        self._tool_catalog = _format_tool_catalog(tools, self._allowed_tools)

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

            repeat_threshold = 3 if tool_name in _LOOP_TOLERANT_TOOLS else 2
            candidate_args = decision.get("args") or {}
            window = history[-repeat_threshold:]
            if len(window) >= repeat_threshold and all(
                prior.tool == tool_name and prior.args == candidate_args
                for prior in window
            ):
                final_reasoning = (
                    f"agent looped on {tool_name} with identical args; aborting"
                )
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

            if _is_app_launch(tool_name, args) and status == "success":
                await asyncio.sleep(_APP_LAUNCH_PAUSE_SECONDS)
            elif tool_name in _POST_INPUT_PAUSE_TOOLS and status == "success":
                await asyncio.sleep(_POST_INPUT_PAUSE_SECONDS)

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
        if prev.tool in _OBSERVATION_SKIP_TOOLS:
            return True
        if prev.tool == "baselithbot_shell_run" and not _is_app_launch(
            prev.tool, prev.args
        ):
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
        window = history[-_HISTORY_CONTEXT_WINDOW:]
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
            f"{_SYSTEM_PROMPT_HEAD}\n\n"
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
                timeout=_VISION_STEP_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "desktop_agent_vision_timeout",
                seconds=_VISION_STEP_TIMEOUT_SECONDS,
            )
            return {
                "tool": "fail",
                "reasoning": (
                    f"vision model exceeded {_VISION_STEP_TIMEOUT_SECONDS:.0f}s idle "
                    "budget on a single decision"
                ),
            }
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
                    f"model did not return JSON; content: {response.content[:200]!r}"
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
