"""Chat-command handler implementing OpenClaw's slash command surface.

Supported commands (mirroring OpenClaw's ``/help``):
``/status``, ``/new``, ``/reset``, ``/compact``, ``/think``, ``/verbose``,
``/trace``, ``/usage``, ``/restart``, ``/activation``.

Each command returns a structured dict; the orchestrator decides how to
render it. ``ChatCommandRouter`` is intentionally side-effect-free for
unknown commands to keep handler composition predictable.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

CommandHandler = Callable[[list[str], dict[str, Any]], Awaitable[dict[str, Any]]]

SUPPORTED_COMMANDS: tuple[str, ...] = (
    "status",
    "new",
    "reset",
    "compact",
    "think",
    "verbose",
    "trace",
    "usage",
    "restart",
    "activation",
)


class ChatCommandRouter:
    """Parse and dispatch ``/command args`` strings."""

    def __init__(self) -> None:
        self._handlers: dict[str, CommandHandler] = {}
        self._stats: dict[str, int] = {cmd: 0 for cmd in SUPPORTED_COMMANDS}
        self._started_at = time.time()

    def register(self, name: str, handler: CommandHandler) -> None:
        if name not in SUPPORTED_COMMANDS:
            raise KeyError(f"command '/{name}' is not in SUPPORTED_COMMANDS")
        self._handlers[name] = handler

    def supported(self) -> list[str]:
        return list(SUPPORTED_COMMANDS)

    async def handle(
        self, line: str, context: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        ctx = context or {}
        if not line.startswith("/"):
            return {"status": "ignored", "reason": "not a slash command"}

        tokens = line[1:].strip().split()
        if not tokens:
            return {"status": "error", "error": "empty command"}

        name, *args = tokens
        if name not in SUPPORTED_COMMANDS:
            return {
                "status": "unknown",
                "command": name,
                "supported": list(SUPPORTED_COMMANDS),
            }
        self._stats[name] += 1

        handler = self._handlers.get(name)
        if handler is not None:
            return await handler(args, ctx)
        return self._default_response(name, args)

    def _default_response(self, name: str, args: list[str]) -> dict[str, Any]:
        if name == "status":
            return {
                "command": name,
                "uptime_seconds": time.time() - self._started_at,
                "stats": dict(self._stats),
            }
        return {
            "command": name,
            "args": args,
            "status": "ack",
            "note": "default handler — register a custom one via .register()",
        }


__all__ = ["ChatCommandRouter", "SUPPORTED_COMMANDS", "CommandHandler"]
