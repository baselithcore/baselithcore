"""Microsoft Teams adapter via Incoming Webhook."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class MicrosoftTeamsAdapter(ChannelAdapter):
    """POST messages to a Teams channel via an Incoming Webhook URL."""

    name = "microsoft_teams"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        card = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": message.metadata.get("summary", "baselithbot"),
            "themeColor": message.metadata.get("theme_color", "0078D7"),
            "title": message.metadata.get("title", message.target or "baselithbot"),
            "text": message.text,
        }
        return await self._deliver_via_pool(self._config["webhook_url"], json=card)


__all__ = ["MicrosoftTeamsAdapter"]
