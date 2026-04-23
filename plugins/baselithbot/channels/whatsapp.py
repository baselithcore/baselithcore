"""WhatsApp Business Cloud API adapter."""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


class WhatsAppAdapter(ChannelAdapter):
    """Send messages via Meta's WhatsApp Business Cloud API."""

    name = "whatsapp"
    requires_credentials = ("access_token", "phone_number_id")

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "status": "unconfigured",
                "missing": ["access_token", "phone_number_id"],
            }
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        api_version = self._config.get("api_version", "v19.0")
        url = f"https://graph.facebook.com/{api_version}/{self._config['phone_number_id']}/messages"
        headers = {
            "Authorization": f"Bearer {self._config['access_token']}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.target,
            "type": "text",
            "text": {"body": message.text, "preview_url": False},
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["WhatsAppAdapter"]
