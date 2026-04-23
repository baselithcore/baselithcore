"""Default real handlers for OpenClaw-style slash commands.

Wires ``ChatCommandRouter`` to live runtime state (UsageLedger,
SessionManager, log level, tracer toggle, restart marker, activation flag).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from plugins.baselithbot.chat.commands import ChatCommandRouter
from plugins.baselithbot.observability.usage import UsageLedger
from plugins.baselithbot.sessions import SessionManager


class SlashRuntimeState:
    """Mutable runtime knobs flipped by slash commands."""

    def __init__(self) -> None:
        self.verbose: bool = False
        self.trace: bool = False
        self.activation: bool = True
        self.restart_requested: bool = False
        self.last_compact_at: float | None = None


def install_default_handlers(
    router: ChatCommandRouter,
    *,
    sessions: SessionManager,
    usage: UsageLedger,
    state: SlashRuntimeState | None = None,
) -> SlashRuntimeState:
    """Bind the canonical slash commands to live runtime helpers."""
    state = state or SlashRuntimeState()

    async def _new(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        title = " ".join(args) or context.get("title", "")
        primary = bool(context.get("primary", False))
        session = sessions.create(title=title, primary=primary)
        return {"command": "new", "session": session.model_dump()}

    async def _reset(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        sid = (args[0] if args else context.get("session_id")) or ""
        if not sid:
            return {"command": "reset", "status": "missing_session_id"}
        sessions.reset(sid)
        return {"command": "reset", "session_id": sid, "status": "cleared"}

    async def _compact(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del args, context
        state.last_compact_at = time.time()
        return {
            "command": "compact",
            "status": "marker_set",
            "ts": state.last_compact_at,
        }

    async def _think(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del context
        text = " ".join(args)
        return {"command": "think", "thought": text or "(empty)", "private": True}

    async def _verbose(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del context
        if args and args[0].lower() in ("off", "false", "0"):
            state.verbose = False
        else:
            state.verbose = True
        logging.getLogger().setLevel(logging.DEBUG if state.verbose else logging.INFO)
        return {"command": "verbose", "enabled": state.verbose}

    async def _trace(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del context
        state.trace = bool(not args or args[0].lower() not in ("off", "false", "0"))
        return {"command": "trace", "enabled": state.trace}

    async def _usage(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del args, context
        return {
            "command": "usage",
            **usage.summary(),
            "by_model": usage.by_model_breakdown(),
        }

    async def _restart(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del args, context
        state.restart_requested = True
        return {
            "command": "restart",
            "status": "requested",
            "note": "the supervisor must observe restart_requested and act",
        }

    async def _activation(args: list[str], context: dict[str, Any]) -> dict[str, Any]:
        del context
        if args and args[0].lower() in ("off", "false", "0", "disable"):
            state.activation = False
        elif args and args[0].lower() in ("on", "true", "1", "enable"):
            state.activation = True
        return {"command": "activation", "enabled": state.activation}

    router.register("new", _new)
    router.register("reset", _reset)
    router.register("compact", _compact)
    router.register("think", _think)
    router.register("verbose", _verbose)
    router.register("trace", _trace)
    router.register("usage", _usage)
    router.register("restart", _restart)
    router.register("activation", _activation)
    return state


__all__ = ["SlashRuntimeState", "install_default_handlers"]
