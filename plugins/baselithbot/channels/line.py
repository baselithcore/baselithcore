"""LINE Messaging API adapter (push message)."""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


class LineAdapter(ChannelAdapter):
    """Push text messages via the LINE Messaging API."""

    name = "line"
    requires_credentials = ("channel_access_token",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["channel_access_token"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Authorization": f"Bearer {self._config['channel_access_token']}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": message.target,
            "messages": [{"type": "text", "text": message.text}],
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["LineAdapter"]
