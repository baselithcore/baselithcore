"""Default inbound handler bridging channel events → sessions + event bus.

Turns every ``POST /api/baselithbot/inbound/{channel}`` into a pair of
session messages: an inbound user message recording what the sender
said, plus a bus publish so the dashboard SSE stream can surface the
event in real time.

Sessions are keyed by ``f"ch:{channel}:{sender}"`` as the title so the
operator can recognise them in the Sessions tab. The session id stays
uuid-based (created lazily the first time a given ``(channel, sender)``
pair appears).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from plugins.baselithbot.dashboard.bus import _BUS
from plugins.baselithbot.inbound.dispatcher import InboundEvent
from plugins.baselithbot.sessions.manager import SessionMessage

if TYPE_CHECKING:
    from plugins.baselithbot.plugin import BaselithbotPlugin


logger = get_logger(__name__)


_SESSION_TITLE_TEMPLATE = "ch:{channel}:{sender}"


def register_default_inbound_handlers(plugin: BaselithbotPlugin) -> None:
    """Register one handler per known channel routing events to sessions."""
    dispatcher = plugin.inbound_dispatcher
    sessions = plugin.sessions

    def _session_for(event: InboundEvent) -> str:
        title = _SESSION_TITLE_TEMPLATE.format(
            channel=event.channel,
            sender=event.sender or "anonymous",
        )
        for existing in sessions.list():
            if existing.title == title:
                return existing.id
        return sessions.create(title=title).id

    async def _handle(event: InboundEvent) -> dict[str, Any]:
        sid = _session_for(event)
        message = SessionMessage(
            role="user",
            content=event.text,
            metadata={
                "channel": event.channel,
                "sender": event.sender,
                "inbound": True,
                "raw": event.raw,
            },
        )
        sessions.send(sid, message)
        _BUS.publish(
            "channel.inbound",
            {
                "channel": event.channel,
                "sender": event.sender,
                "session_id": sid,
                "text_preview": event.text[:120],
            },
        )
        logger.info(
            "baselithbot_channel_inbound_routed",
            channel=event.channel,
            session_id=sid,
        )
        return {"status": "routed", "session_id": sid}

    for channel in plugin.channels.known():
        dispatcher.register(channel, _handle)


__all__ = ["register_default_inbound_handlers"]
