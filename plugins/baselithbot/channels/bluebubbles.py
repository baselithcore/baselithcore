"""BlueBubbles iMessage bridge adapter (HTTP REST)."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class BlueBubblesAdapter(ChannelAdapter):
    """Send iMessages via a local BlueBubbles server."""

    name = "bluebubbles"
    requires_credentials = ("server_url", "password")

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["server_url", "password"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        base = self._config["server_url"].rstrip("/")
        url = f"{base}/api/v1/message/text?password={self._config['password']}"
        payload = {
            "chatGuid": message.target,
            "tempGuid": message.metadata.get("temp_guid", "baselithbot"),
            "message": message.text,
            "method": message.metadata.get("method", "apple-script"),
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["BlueBubblesAdapter"]
