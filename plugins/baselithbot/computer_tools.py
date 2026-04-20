"""MCP tool definitions for the Computer Use layer.

Exposes desktop screenshot, mouse, keyboard, shell, and filesystem tools.
All tools require ``ComputerUseConfig.enabled`` and the matching capability
flag; otherwise they return ``{"status": "denied", "error": "..."}`` rather
than raising.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

from .approvals import ApprovalGate
from .computer_use import (
    AuditLogger,
    ComputerUseConfig,
    ComputerUseError,
)
from .desktop_vision import DesktopVision
from .filesystem import ScopedFileSystem
from .os_control import OSController
from .shell_exec import ShellExecutor
from .spotify_control import SpotifyController
from .web_launcher import WebLauncher

logger = get_logger(__name__)


def _denied(exc: ComputerUseError) -> dict[str, Any]:
    return {"status": "denied", "error": str(exc)}


def _error(tool: str, exc: Exception) -> dict[str, Any]:
    logger.error("baselithbot_computer_tool_error", tool=tool, error=str(exc))
    return {"status": "error", "error": str(exc)}


def build_computer_tool_definitions(
    config: ComputerUseConfig,
    approvals: ApprovalGate | None = None,
) -> list[dict[str, Any]]:
    """Build the Computer Use MCP tool list bound to a single config.

    Passing an :class:`ApprovalGate` enables human-in-the-loop approval for
    every capability listed in ``config.require_approval_for``.
    """
    audit = AuditLogger(config.audit_log_path)
    os_ctrl = OSController(config, audit, approvals=approvals)
    vision = DesktopVision(config, audit)
    shell = ShellExecutor(config, audit, approvals=approvals)
    fs = ScopedFileSystem(config, audit, approvals=approvals)
    spotify = SpotifyController(config, audit, approvals=approvals)
    web = WebLauncher(config, audit, approvals=approvals)

    async def desktop_screenshot(
        monitor: int = 1,
        image_format: str = "PNG",
        quality: int = 80,
    ) -> dict[str, Any]:
        try:
            b64 = await vision.screenshot(
                monitor=monitor, image_format=image_format, quality=quality
            )
            return {
                "status": "success",
                "screenshot_base64": b64,
                "monitor": monitor,
                "format": image_format.upper(),
            }
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("desktop_screenshot", exc)

    async def screen_size() -> dict[str, Any]:
        try:
            w, h = await os_ctrl.screen_size()
            return {"status": "success", "width": w, "height": h}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("screen_size", exc)

    async def mouse_move(x: int, y: int, duration: float = 0.0) -> dict[str, Any]:
        try:
            await os_ctrl.mouse_move(x, y, duration)
            return {"status": "success", "x": x, "y": y}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("mouse_move", exc)

    async def mouse_click(
        x: int | None = None,
        y: int | None = None,
        button: str = "left",
        clicks: int = 1,
    ) -> dict[str, Any]:
        try:
            await os_ctrl.mouse_click(x=x, y=y, button=button, clicks=clicks)
            return {"status": "success", "x": x, "y": y, "button": button}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("mouse_click", exc)

    async def mouse_scroll(amount: int) -> dict[str, Any]:
        try:
            await os_ctrl.mouse_scroll(amount)
            return {"status": "success", "amount": amount}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("mouse_scroll", exc)

    async def kbd_type(text: str, interval: float = 0.0) -> dict[str, Any]:
        try:
            await os_ctrl.kbd_type(text, interval=interval)
            return {"status": "success", "length": len(text)}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("kbd_type", exc)

    async def kbd_press(key: str) -> dict[str, Any]:
        try:
            await os_ctrl.kbd_press(key)
            return {"status": "success", "key": key}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("kbd_press", exc)

    async def kbd_hotkey(keys: list[str]) -> dict[str, Any]:
        try:
            await os_ctrl.kbd_hotkey(*keys)
            return {"status": "success", "keys": keys}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("kbd_hotkey", exc)

    async def shell_run(command: str, cwd: str | None = None) -> dict[str, Any]:
        try:
            out = await shell.run(command, cwd=cwd)
            return {"status": "success", **out}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("shell_run", exc)

    async def fs_read(path: str) -> dict[str, Any]:
        try:
            return {"status": "success", **await fs.read(path)}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("fs_read", exc)

    async def fs_write(path: str, content: str) -> dict[str, Any]:
        try:
            return {"status": "success", **await fs.write(path, content)}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("fs_write", exc)

    async def fs_list(path: str = ".") -> dict[str, Any]:
        try:
            return {"status": "success", **await fs.list_dir(path)}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("fs_list", exc)

    async def spotify_control(action: str, uri: str | None = None) -> dict[str, Any]:
        try:
            result = await spotify.run(action, uri=uri)  # type: ignore[arg-type]
            return {"status": "success", **result}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("spotify_control", exc)

    async def open_url(url: str) -> dict[str, Any]:
        try:
            return {"status": "success", **await web.open_url(url)}
        except ComputerUseError as exc:
            return _denied(exc)
        except Exception as exc:
            return _error("open_url", exc)

    return [
        {
            "name": "baselithbot_desktop_screenshot",
            "description": "Capture a base64 screenshot (PNG default, JPEG/WEBP for smaller payloads).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "monitor": {"type": "integer", "default": 1},
                    "image_format": {
                        "type": "string",
                        "enum": ["PNG", "JPEG", "WEBP"],
                        "default": "PNG",
                    },
                    "quality": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 80,
                    },
                },
            },
            "handler": desktop_screenshot,
        },
        {
            "name": "baselithbot_screen_size",
            "description": "Return primary screen size in pixels.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": screen_size,
        },
        {
            "name": "baselithbot_mouse_move",
            "description": "Move the mouse cursor to absolute (x, y).",
            "input_schema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "duration": {"type": "number", "default": 0.0},
                },
                "required": ["x", "y"],
            },
            "handler": mouse_move,
        },
        {
            "name": "baselithbot_mouse_click",
            "description": "Click the mouse at (x, y) or current position.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "default": "left",
                    },
                    "clicks": {"type": "integer", "default": 1},
                },
            },
            "handler": mouse_click,
        },
        {
            "name": "baselithbot_mouse_scroll",
            "description": "Scroll the mouse wheel by the given amount.",
            "input_schema": {
                "type": "object",
                "properties": {"amount": {"type": "integer"}},
                "required": ["amount"],
            },
            "handler": mouse_scroll,
        },
        {
            "name": "baselithbot_kbd_type",
            "description": "Type text via the keyboard.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "interval": {"type": "number", "default": 0.0},
                },
                "required": ["text"],
            },
            "handler": kbd_type,
        },
        {
            "name": "baselithbot_kbd_press",
            "description": "Press a single key (e.g. 'enter', 'tab').",
            "input_schema": {
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            "handler": kbd_press,
        },
        {
            "name": "baselithbot_kbd_hotkey",
            "description": "Press a key combination (e.g. ['ctrl', 'c']).",
            "input_schema": {
                "type": "object",
                "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                "required": ["keys"],
            },
            "handler": kbd_hotkey,
        },
        {
            "name": "baselithbot_shell_run",
            "description": (
                "Run an allowlisted shell command. The command string is "
                "parsed with shlex and executed via subprocess with "
                "shell=False, so pipes (|), redirects (> < >> <<), chaining "
                "(; && ||), background (&), command substitution ($(...) or "
                "backticks), and subshells are NOT supported and will be "
                "rejected. Each invocation must be a single binary plus "
                "literal arguments. Example: use 'ipconfig getifaddr en0' to "
                "retrieve the primary IPv4 address on macOS, not "
                "'ifconfig | grep inet'. Requires computer_use.allow_shell "
                "and the first token present in allowed_shell_commands."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                },
                "required": ["command"],
            },
            "handler": shell_run,
        },
        {
            "name": "baselithbot_fs_read",
            "description": "Read a UTF-8 text file under the configured filesystem_root.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            "handler": fs_read,
        },
        {
            "name": "baselithbot_fs_write",
            "description": "Write a UTF-8 text file under filesystem_root.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            "handler": fs_write,
        },
        {
            "name": "baselithbot_fs_list",
            "description": "List the contents of a directory under filesystem_root.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
            "handler": fs_list,
        },
        {
            "name": "baselithbot_spotify",
            "description": (
                "Deterministic Spotify control on macOS via AppleScript. "
                "Prefer this over mouse/kbd when the goal is playback. "
                "Actions: 'play' (resumes last context), 'pause', 'toggle', "
                "'next', 'previous', 'play_uri' (requires uri starting with "
                "'spotify:'), 'status' (returns current track metadata). "
                "Requires allow_shell=true AND 'osascript' in the shell "
                "allowlist."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "play",
                            "pause",
                            "toggle",
                            "next",
                            "previous",
                            "play_uri",
                            "status",
                        ],
                    },
                    "uri": {
                        "type": "string",
                        "description": (
                            "Spotify URI (required only when action='play_uri'). "
                            "Example: 'spotify:playlist:37i9dQZEVXbMDoHDwVN2tF'."
                        ),
                    },
                },
                "required": ["action"],
            },
            "handler": spotify_control,
        },
        {
            "name": "baselithbot_open_url",
            "description": (
                "Open a URL in the OS-default browser (macOS `open`, Linux "
                "`xdg-open`). Deterministic entry point for every web task "
                "(gmail, calendar, amazon, youtube, news, docs). Prefer this "
                "over automating cmd+L / address bar typing. "
                "Only http(s), mailto, and tel schemes are accepted. Requires "
                "allow_shell=true AND the OS launcher binary ('open' on "
                "macOS) in the Shell allowlist."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Fully-qualified URL, e.g. 'https://mail.google.com' "
                            "or 'mailto:me@example.com'."
                        ),
                    },
                },
                "required": ["url"],
            },
            "handler": open_url,
        },
    ]


__all__ = ["build_computer_tool_definitions"]
