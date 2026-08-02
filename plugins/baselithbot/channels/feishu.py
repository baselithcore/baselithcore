"""Feishu (Lark) adapter via Incoming Webhook."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class FeishuAdapter(ChannelAdapter):
    """POST messages to a Feishu chat via Incoming Webhook URL."""

    name = "feishu"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {
            "msg_type": "text",
            "content": {"text": message.text},
        }

        return await self._deliver_via_pool(self._config["webhook_url"], json=payload)


__all__ = ["FeishuAdapter"]
