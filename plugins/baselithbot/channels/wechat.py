"""WeChat Work (企业微信) adapter via group webhook."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage
from plugins.baselithbot.http import hardened_client


class WeChatAdapter(ChannelAdapter):
    """Push text messages to a WeChat Work group via webhook key."""

    name = "wechat"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {"msgtype": "text", "text": {"content": message.text}}
        async with hardened_client(timeout=15.0) as client:
            response = await client.post(self._config["webhook_url"], json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["WeChatAdapter"]
