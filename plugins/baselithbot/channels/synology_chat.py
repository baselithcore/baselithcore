"""Synology Chat adapter via Incoming Webhook (form-encoded)."""

from __future__ import annotations

import json
from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage
from plugins.baselithbot.http import hardened_client


class SynologyChatAdapter(ChannelAdapter):
    """Push messages to a Synology Chat channel via Incoming Webhook URL."""

    name = "synology_chat"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {"text": message.text}
        data = {"payload": json.dumps(payload)}

        async with hardened_client(timeout=15.0) as client:
            response = await client.post(self._config["webhook_url"], data=data)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["SynologyChatAdapter"]
