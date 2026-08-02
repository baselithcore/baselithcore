"""Google Chat adapter via Incoming Webhook."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class GoogleChatAdapter(ChannelAdapter):
    """POST messages to a Google Chat space via Incoming Webhook URL."""

    name = "google_chat"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload: dict[str, Any] = {"text": message.text}
        if "thread_key" in message.metadata:
            payload["thread"] = {"name": message.metadata["thread_key"]}

        return await self._deliver_via_pool(self._config["webhook_url"], json=payload)


__all__ = ["GoogleChatAdapter"]
