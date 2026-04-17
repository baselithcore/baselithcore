"""Google Chat adapter via Incoming Webhook."""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


class GoogleChatAdapter(ChannelAdapter):
    """POST messages to a Google Chat space via Incoming Webhook URL."""

    name = "google_chat"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        payload: dict[str, Any] = {"text": message.text}
        if "thread_key" in message.metadata:
            payload["thread"] = {"name": message.metadata["thread_key"]}

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self._config["webhook_url"], json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["GoogleChatAdapter"]
