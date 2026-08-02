"""WeChat Work (企业微信) adapter via group webhook."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class WeChatAdapter(ChannelAdapter):
    """Push text messages to a WeChat Work group via webhook key."""

    name = "wechat"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {"msgtype": "text", "text": {"content": message.text}}
        return await self._deliver_via_pool(self._config["webhook_url"], json=payload)


__all__ = ["WeChatAdapter"]
