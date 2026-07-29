"""Discord webhook adapter."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class DiscordAdapter(ChannelAdapter):
    """POST messages to a Discord channel via a webhook URL."""

    name = "discord"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {
            "content": message.text,
            "username": message.metadata.get("username", "baselithbot"),
        }
        return await self._deliver_via_pool(self._config["webhook_url"], json=payload)


__all__ = ["DiscordAdapter"]
