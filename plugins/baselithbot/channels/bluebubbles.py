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
        base = self._config["server_url"].rstrip("/")
        url = f"{base}/api/v1/message/text?password={self._config['password']}"
        payload = {
            "chatGuid": message.target,
            "tempGuid": message.metadata.get("temp_guid", "baselithbot"),
            "message": message.text,
            "method": message.metadata.get("method", "apple-script"),
        }

        return await self._deliver_via_pool(url, timeout=20.0, json=payload)


__all__ = ["BlueBubblesAdapter"]
