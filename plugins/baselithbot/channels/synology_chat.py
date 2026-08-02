"""Synology Chat adapter via Incoming Webhook (form-encoded)."""

from __future__ import annotations

import json
from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class SynologyChatAdapter(ChannelAdapter):
    """Push messages to a Synology Chat channel via Incoming Webhook URL."""

    name = "synology_chat"
    requires_credentials = ("webhook_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["webhook_url"]}
        payload = {"text": message.text}
        data = {"payload": json.dumps(payload)}

        return await self._deliver_via_pool(self._config["webhook_url"], data=data)


__all__ = ["SynologyChatAdapter"]
