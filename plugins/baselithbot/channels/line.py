"""LINE Messaging API adapter (push message)."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class LineAdapter(ChannelAdapter):
    """Push text messages via the LINE Messaging API."""

    name = "line"
    requires_credentials = ("channel_access_token",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["channel_access_token"]}
        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Authorization": f"Bearer {self._config['channel_access_token']}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": message.target,
            "messages": [{"type": "text", "text": message.text}],
        }

        return await self._deliver_via_pool(url, headers=headers, json=payload)


__all__ = ["LineAdapter"]
