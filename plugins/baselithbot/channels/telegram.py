"""Telegram Bot API adapter."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class TelegramAdapter(ChannelAdapter):
    """Send messages via the Telegram Bot HTTP API."""

    name = "telegram"
    requires_credentials = ("bot_token",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["bot_token"]}
        token = self._config["bot_token"]
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict[str, Any] = {
            "chat_id": message.target,
            "text": message.text,
        }
        if "parse_mode" in message.metadata:
            payload["parse_mode"] = message.metadata["parse_mode"]

        return await self._deliver_via_pool(url, json=payload)


__all__ = ["TelegramAdapter"]
