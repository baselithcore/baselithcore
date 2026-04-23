"""Tool-policy filtering and system-prompt catalog rendering."""

from __future__ import annotations

import json
from typing import Any

from plugins.baselithbot.computer_use.config import ComputerUseConfig

EMPTY_POLICY_REASON = (
    "No Computer Use capabilities are available. Enable the master switch and "
    "the required capabilities on the Computer Use page, then try again."
)

_POLICY_TO_TOOL_NAMES: dict[str, list[str]] = {
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


def format_tool_catalog(tools: dict[str, dict[str, Any]], allowed_names: list[str]) -> str:
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


def filter_tools_by_policy(
    tools: dict[str, dict[str, Any]], policy: ComputerUseConfig
) -> list[str]:
    """Return the tool names that are actually reachable under ``policy``."""
    if not policy.enabled:
        return []
    allowed: list[str] = []
    for attr, names in _POLICY_TO_TOOL_NAMES.items():
        if getattr(policy, attr, False):
            allowed.extend(n for n in names if n in tools)
    return allowed


__all__ = [
    "EMPTY_POLICY_REASON",
    "filter_tools_by_policy",
    "format_tool_catalog",
]
