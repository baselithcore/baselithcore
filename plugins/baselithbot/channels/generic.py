"""Generic webhook adapter used as fallback for stub channels.

Channels for which Baselithbot has no first-party SDK adapter still gain
outbound delivery via this generic POST-to-webhook adapter. Required
config: ``webhook_url``. Payload shape mirrors the ``ChannelMessage``.
"""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class GenericWebhookAdapter(ChannelAdapter):
    """POST ``ChannelMessage`` JSON to a configured webhook URL."""

    requires_credentials = ("webhook_url",)

    def __init__(
        self,
        name: str = "generic-webhook",
        config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(config)
        self.name = name

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "status": "unconfigured",
                "channel": self.name,
                "missing": list(self.requires_credentials),
            }
        url = self._config["webhook_url"]
        payload = {
            "channel": self.name,
            "target": message.target,
            "text": message.text,
            "metadata": message.metadata,
        }
        return await self._deliver_via_pool(url, json=payload)


__all__ = ["GenericWebhookAdapter"]
