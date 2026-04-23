"""Mattermost adapter via Incoming Webhook."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class MattermostAdapter(ChannelAdapter):
    """POST messages to a Mattermost channel via Incoming Webhook URL."""

    name = "mattermost"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        payload = {
            "text": message.text,
            "channel": message.target or self._config.get("default_channel"),
            "username": message.metadata.get("username", "baselithbot"),
            "icon_url": message.metadata.get("icon_url"),
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self._config["webhook_url"], json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["MattermostAdapter"]
