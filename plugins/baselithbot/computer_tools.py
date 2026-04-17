"""MCP tool definitions for the Computer Use layer.

Exposes desktop screenshot, mouse, keyboard, shell, and filesystem tools.
All tools require ``ComputerUseConfig.enabled`` and the matching capability
flag; otherwise they return ``{"status": "denied", "error": "..."}`` rather
than raising.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger

from .computer_use import (
    AuditLogger,
    ComputerUseConfig,
    ComputerUseError,
)
from .desktop_vision import DesktopVision
from .filesystem import ScopedFileSystem
from .os_control import OSController
from .shell_exec import ShellExecutor

logger = get_logger(__name__)


def _denied(exc: ComputerUseError) -> dict[str, Any]:
    return {"status": "denied", "error": str(exc)}


def _error(tool: str, exc: Exception) -> dict[str, Any]:
    logger.error("baselithbot_computer_tool_error", tool=tool, error=str(exc))
    return {"status": "error", "error": str(exc)}


def build_computer_tool_definitions(
    config: ComputerUseConfig,
) -> list[dict[str, Any]]:
    """Build the Computer Use MCP tool list bound to a single config."""
    audit = AuditLogger(config.audit_log_path)
    os_ctrl = OSController(config, audit)
    vision = DesktopVision(config, audit)
    shell = ShellExecutor(config, audit)
    fs = ScopedFileSystem(config, audit)

    async def desktop_screenshot(monitor: int = 1) -> dict[str, Any]:
        try:
            b64 = await vision.screenshot(monitor=monitor)
            return {"status": "success", "screenshot_base64": b64, "monitor": monitor}
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

    return [
        {
            "name": "baselithbot_desktop_screenshot",
            "description": "Capture a base64 PNG screenshot of a monitor.",
            "input_schema": {
                "type": "object",
                "properties": {"monitor": {"type": "integer", "default": 1}},
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
                "Run an allowlisted shell command (shell=False, argv split via shlex). "
                "Requires computer_use.allow_shell + allowed_shell_commands."
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
    ]


__all__ = ["build_computer_tool_definitions"]
