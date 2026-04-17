"""MCP tools exposing OpenClaw-parity surfaces (channels/sessions/voice/canvas).

Exposed tools:
    - ``baselithbot_channel_list`` / ``_channel_send``
    - ``baselithbot_session_create`` / ``_session_list`` / ``_session_history``
      / ``_session_send`` / ``_session_reset``
    - ``baselithbot_chat_command`` (slash-command dispatcher)
    - ``baselithbot_doctor`` (environment health)
    - ``baselithbot_skills_list`` / ``_skills_inject``
    - ``baselithbot_voice_tts`` (system fallback)
    - ``baselithbot_canvas_render``
    - ``baselithbot_cron_list``
    - ``baselithbot_tailscale_status``
    - ``baselithbot_node_pairing_token`` / ``_paired_nodes``
"""

from __future__ import annotations

import time
from typing import Any

from core.observability.logging import get_logger

from .canvas import (
    A2UIRenderer,
    CanvasSurface,
    CanvasWidgetError,
    build_widgets,
)
from .channels import ChannelMessage, ChannelRegistry, build_default_registry
from .chat_commands import ChatCommandRouter
from .cron import CronScheduler
from .doctor import run_doctor
from .gateway import TailscaleGateway
from .nodes import NodePairing
from .sessions import SessionManager, SessionMessage
from .skills import SkillRegistry, load_injection_bundle
from .voice import SystemTTS

logger = get_logger(__name__)


def _err(tool: str, exc: Exception) -> dict[str, Any]:
    logger.error("baselithbot_openclaw_tool_error", tool=tool, error=str(exc))
    return {"status": "error", "error": str(exc), "tool": tool}


def build_openclaw_tool_definitions(
    *,
    channels: ChannelRegistry | None = None,
    sessions: SessionManager | None = None,
    chat_commands: ChatCommandRouter | None = None,
    skills: SkillRegistry | None = None,
    cron: CronScheduler | None = None,
    pairing: NodePairing | None = None,
    canvas: CanvasSurface | None = None,
) -> list[dict[str, Any]]:
    """Build the OpenClaw-parity MCP tool list bound to shared state."""
    channels = channels or build_default_registry()
    sessions = sessions or SessionManager()
    chat_commands = chat_commands or ChatCommandRouter()
    skills = skills or SkillRegistry()
    cron = cron or CronScheduler()
    pairing = pairing or NodePairing()
    canvas = canvas or CanvasSurface()
    canvas_renderer = A2UIRenderer()
    tts = SystemTTS()

    async def channel_list() -> dict[str, Any]:
        return {"status": "success", "channels": channels.known()}

    async def channel_send(
        channel: str,
        target: str,
        text: str,
        config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            msg = ChannelMessage(
                channel=channel,
                target=target,
                text=text,
                metadata=metadata or {},
            )
            return {
                "status": "success",
                **(await channels.send(channel, msg, config or {})),
            }
        except Exception as exc:
            return _err("channel_send", exc)

    async def session_create(title: str = "", primary: bool = False) -> dict[str, Any]:
        return {
            "status": "success",
            "session": sessions.create(title=title, primary=primary).model_dump(),
        }

    async def session_list() -> dict[str, Any]:
        return {
            "status": "success",
            "sessions": [s.model_dump() for s in sessions.list()],
        }

    async def session_history(session_id: str, limit: int = 50) -> dict[str, Any]:
        return {
            "status": "success",
            "history": [
                m.model_dump() for m in sessions.history(session_id, limit=limit)
            ],
        }

    async def session_send(session_id: str, role: str, content: str) -> dict[str, Any]:
        try:
            msg = sessions.send(session_id, SessionMessage(role=role, content=content))
            return {"status": "success", "message": msg.model_dump()}
        except KeyError as exc:
            return _err("session_send", exc)

    async def session_reset(session_id: str) -> dict[str, Any]:
        sessions.reset(session_id)
        return {"status": "success", "session_id": session_id}

    async def chat_command(line: str) -> dict[str, Any]:
        return await chat_commands.handle(line)

    async def doctor() -> dict[str, Any]:
        return {"status": "success", **await run_doctor()}

    async def skills_list() -> dict[str, Any]:
        return {
            "status": "success",
            "skills": [s.model_dump() for s in skills.list()],
        }

    async def skills_inject(root: str) -> dict[str, Any]:
        bundle = load_injection_bundle(root)
        return {
            "status": "success",
            "sources": bundle.sources,
            "prompt_block_chars": len(bundle.to_prompt_block()),
        }

    async def voice_tts(text: str, voice: str | None = None) -> dict[str, Any]:
        return {"status": "queued", **await tts.synthesize(text, voice=voice)}

    async def canvas_render(
        widgets: list[dict[str, Any]] | None = None,
        clear: bool = False,
    ) -> dict[str, Any]:
        if clear:
            canvas.clear()
        try:
            parsed = build_widgets(widgets)
        except CanvasWidgetError as exc:
            return _err("canvas_render", exc)
        canvas.extend(parsed)
        message = canvas_renderer.render(canvas, generated_at=time.time())
        return {"status": "success", "a2ui": message.model_dump()}

    async def cron_list() -> dict[str, Any]:
        return {"status": "success", "jobs": cron.list()}

    async def tailscale_status() -> dict[str, Any]:
        status = await TailscaleGateway.status()
        return {"status": "success", **status.model_dump()}

    async def node_pairing_token(platform: str | None = None) -> dict[str, Any]:
        return {
            "status": "success",
            "token": pairing.issue_token(platform=platform),
            "expires_in_seconds": 300,
        }

    async def paired_nodes() -> dict[str, Any]:
        return {
            "status": "success",
            "nodes": [n.model_dump() for n in pairing.list_paired()],
            **pairing.status(),
        }

    return [
        {
            "name": "baselithbot_channel_list",
            "description": "List every supported messaging channel.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": channel_list,
        },
        {
            "name": "baselithbot_channel_send",
            "description": "Deliver a message via a configured channel.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "channel": {"type": "string"},
                    "target": {"type": "string"},
                    "text": {"type": "string"},
                    "config": {"type": "object"},
                    "metadata": {"type": "object"},
                },
                "required": ["channel", "target", "text"],
            },
            "handler": channel_send,
        },
        {
            "name": "baselithbot_session_create",
            "description": "Create a new session.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "primary": {"type": "boolean", "default": False},
                },
            },
            "handler": session_create,
        },
        {
            "name": "baselithbot_session_list",
            "description": "List active sessions.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": session_list,
        },
        {
            "name": "baselithbot_session_history",
            "description": "Return the latest messages for a session.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["session_id"],
            },
            "handler": session_history,
        },
        {
            "name": "baselithbot_session_send",
            "description": "Append a message to a session history.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "role": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["session_id", "role", "content"],
            },
            "handler": session_send,
        },
        {
            "name": "baselithbot_session_reset",
            "description": "Clear the message history of a session.",
            "input_schema": {
                "type": "object",
                "properties": {"session_id": {"type": "string"}},
                "required": ["session_id"],
            },
            "handler": session_reset,
        },
        {
            "name": "baselithbot_chat_command",
            "description": "Run an OpenClaw-style slash command (e.g. /status).",
            "input_schema": {
                "type": "object",
                "properties": {"line": {"type": "string"}},
                "required": ["line"],
            },
            "handler": chat_command,
        },
        {
            "name": "baselithbot_doctor",
            "description": "Run the environment health probe.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": doctor,
        },
        {
            "name": "baselithbot_skills_list",
            "description": "List all registered skills.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": skills_list,
        },
        {
            "name": "baselithbot_skills_inject",
            "description": "Load AGENTS.md/SOUL.md/TOOLS.md from a directory.",
            "input_schema": {
                "type": "object",
                "properties": {"root": {"type": "string"}},
                "required": ["root"],
            },
            "handler": skills_inject,
        },
        {
            "name": "baselithbot_voice_tts",
            "description": "Speak text via the OS TTS fallback.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "voice": {"type": "string"},
                },
                "required": ["text"],
            },
            "handler": voice_tts,
        },
        {
            "name": "baselithbot_canvas_render",
            "description": "Render widgets onto the Live Canvas surface and return A2UI.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "widgets": {"type": "array"},
                    "clear": {"type": "boolean", "default": False},
                },
            },
            "handler": canvas_render,
        },
        {
            "name": "baselithbot_cron_list",
            "description": "List registered cron jobs.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": cron_list,
        },
        {
            "name": "baselithbot_tailscale_status",
            "description": "Query the local Tailscale CLI for connectivity status.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": tailscale_status,
        },
        {
            "name": "baselithbot_node_pairing_token",
            "description": "Issue a short-lived pairing token for a node.",
            "input_schema": {
                "type": "object",
                "properties": {"platform": {"type": "string"}},
            },
            "handler": node_pairing_token,
        },
        {
            "name": "baselithbot_paired_nodes",
            "description": "List currently paired nodes.",
            "input_schema": {"type": "object", "properties": {}},
            "handler": paired_nodes,
        },
    ]


__all__ = ["build_openclaw_tool_definitions"]
