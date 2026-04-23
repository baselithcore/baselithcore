"""Zalo Official Account + Personal adapters via the Zalo Open API."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class ZaloAdapter(ChannelAdapter):
    """Send messages via Zalo OA Open API (HTTP)."""

    name = "zalo"
    requires_credentials = ("access_token",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["access_token"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        url = "https://openapi.zalo.me/v2.0/oa/message"
        headers = {
            "access_token": self._config["access_token"],
            "Content-Type": "application/json",
        }
        payload = {
            "recipient": {"user_id": message.target},
            "message": {"text": message.text},
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, headers=headers, json=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


class ZaloPersonalAdapter(ZaloAdapter):
    """Personal account variant; same transport, distinct registry entry."""

    name = "zalo_personal"


__all__ = ["ZaloAdapter", "ZaloPersonalAdapter"]
