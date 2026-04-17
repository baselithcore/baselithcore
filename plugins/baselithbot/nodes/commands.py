"""Node command families (Connect / Chat / Voice) + dispatcher."""

from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Callable

from pydantic import BaseModel


class CommandFamily(str, Enum):
    CONNECT = "connect"
    CHAT = "chat"
    VOICE = "voice"


class NodeCommand(BaseModel):
    family: CommandFamily
    name: str
    payload: dict[str, Any] = {}


CommandHandler = Callable[[NodeCommand], Awaitable[dict[str, Any]]]


_HANDLERS: dict[tuple[CommandFamily, str], CommandHandler] = {}


def register_handler(
    family: CommandFamily, name: str, handler: CommandHandler
) -> None:
    _HANDLERS[(family, name)] = handler


def known_handlers() -> list[dict[str, str]]:
    return [
        {"family": fam.value, "name": nm}
        for (fam, nm) in sorted(_HANDLERS.keys(), key=lambda k: (k[0].value, k[1]))
    ]


async def route_command(command: NodeCommand) -> dict[str, Any]:
    handler = _HANDLERS.get((command.family, command.name))
    if handler is None:
        return {
            "status": "error",
            "error": f"no handler for {command.family.value}/{command.name}",
        }
    return await handler(command)


__all__ = [
    "CommandFamily",
    "NodeCommand",
    "CommandHandler",
    "register_handler",
    "known_handlers",
    "route_command",
]
