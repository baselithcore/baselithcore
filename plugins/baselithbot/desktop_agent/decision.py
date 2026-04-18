"""Agent-loop constants + result / decision parsers (no Pydantic / no vision)."""

from __future__ import annotations

import json
import re
from typing import Any

APP_LAUNCH_PAUSE_SECONDS = 2.5
POST_INPUT_PAUSE_SECONDS = 0.4
# Hard ceiling per vision decision call — bounded idle time protects the
# agent from a hung model (mirrors OpenClaw's per-step LLM idle timeout).
VISION_STEP_TIMEOUT_SECONDS = 90.0
# How many recent steps of context we surface to the model each turn.
# Older actions are summarised / dropped so the prompt stays bounded.
HISTORY_CONTEXT_WINDOW = 6

POST_INPUT_PAUSE_TOOLS = {
    "baselithbot_kbd_type",
    "baselithbot_kbd_press",
    "baselithbot_kbd_hotkey",
    "baselithbot_mouse_click",
}
# Tools that MUST stop after 2 consecutive identical calls — non-idempotent
# and cheap to check against the loop guard.
LOOP_TOLERANT_TOOLS = {
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
OBSERVATION_SKIP_TOOLS = {
    "baselithbot_spotify",
    "baselithbot_open_url",
    "baselithbot_fs_read",
    "baselithbot_fs_write",
    "baselithbot_fs_list",
    "baselithbot_screen_size",
}

_APP_LAUNCH_RE = re.compile(r"^\s*open\s+-a\s+", re.IGNORECASE)
_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def is_app_launch(tool_name: str, args: dict[str, Any]) -> bool:
    """Detect ``shell_run`` invocations that boot a GUI app via ``open -a``."""
    if tool_name != "baselithbot_shell_run":
        return False
    cmd = args.get("command")
    if not isinstance(cmd, str):
        return False
    return bool(_APP_LAUNCH_RE.match(cmd))


def summarize_result(result: dict[str, Any]) -> str:
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


def parse_decision(raw_content: str) -> dict[str, Any] | None:
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


__all__ = [
    "APP_LAUNCH_PAUSE_SECONDS",
    "HISTORY_CONTEXT_WINDOW",
    "LOOP_TOLERANT_TOOLS",
    "OBSERVATION_SKIP_TOOLS",
    "POST_INPUT_PAUSE_SECONDS",
    "POST_INPUT_PAUSE_TOOLS",
    "VISION_STEP_TIMEOUT_SECONDS",
    "is_app_launch",
    "parse_decision",
    "summarize_result",
]
