"""Slack Incoming Webhook adapter."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class SlackAdapter(ChannelAdapter):
    """POST messages to Slack via an Incoming Webhook URL."""

    name = "slack"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {
            "text": message.text,
            "channel": message.target or self._config.get("default_channel"),
            **message.metadata,
        }
        return await self._deliver_via_pool(self._config["webhook_url"], json=payload)


__all__ = ["SlackAdapter"]
