"""Per-channel inbound payload normalizers (Slack, Telegram, Discord, generic)."""

from __future__ import annotations

from typing import Any

from .dispatcher import InboundEvent


def parse_slack_event(payload: dict[str, Any]) -> InboundEvent:
    event = payload.get("event", {}) or {}
    return InboundEvent(
        channel="slack",
        sender=event.get("user") or event.get("bot_id"),
        text=event.get("text", ""),
        raw=payload,
    )


def parse_telegram_update(payload: dict[str, Any]) -> InboundEvent:
    message = payload.get("message") or payload.get("edited_message") or {}
    sender = (message.get("from") or {}).get("username") or str(
        (message.get("from") or {}).get("id", "")
    )
    return InboundEvent(
        channel="telegram",
        sender=sender or None,
        text=message.get("text", ""),
        raw=payload,
    )


def parse_discord_interaction(payload: dict[str, Any]) -> InboundEvent:
    member = payload.get("member") or {}
    user = member.get("user") or payload.get("user") or {}
    sender = user.get("username") or str(user.get("id", "")) or None
    text = ""
    data = payload.get("data") or {}
    if "name" in data:
        text = data.get("name", "")
    return InboundEvent(channel="discord", sender=sender, text=text, raw=payload)


def parse_generic(channel: str, payload: dict[str, Any]) -> InboundEvent:
    return InboundEvent(
        channel=channel,
        sender=payload.get("sender") or payload.get("from"),
        text=str(payload.get("text") or payload.get("message") or ""),
        raw=payload,
    )


__all__ = [
    "parse_slack_event",
    "parse_telegram_update",
    "parse_discord_interaction",
    "parse_generic",
]
