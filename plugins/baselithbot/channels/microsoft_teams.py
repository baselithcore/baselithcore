"""Microsoft Teams adapter via Incoming Webhook."""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


class MicrosoftTeamsAdapter(ChannelAdapter):
    """POST messages to a Teams channel via an Incoming Webhook URL."""

    name = "microsoft_teams"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        card = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": message.metadata.get("summary", "baselithbot"),
            "themeColor": message.metadata.get("theme_color", "0078D7"),
            "title": message.metadata.get("title", message.target or "baselithbot"),
            "text": message.text,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(self._config["webhook_url"], json=card)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["MicrosoftTeamsAdapter"]
