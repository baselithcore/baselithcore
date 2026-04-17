"""Generic webhook adapter used as fallback for stub channels.

Channels for which Baselithbot has no first-party SDK adapter still gain
outbound delivery via this generic POST-to-webhook adapter. Required
config: ``webhook_url``. Payload shape mirrors the ``ChannelMessage``.
"""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


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
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["GenericWebhookAdapter"]
