"""Nextcloud Talk adapter via OCS API."""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


class NextcloudTalkAdapter(ChannelAdapter):
    """Send messages via the Nextcloud Talk OCS chat API (basic auth)."""

    name = "nextcloud_talk"
    requires_credentials = ("server_url", "username", "password")

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "status": "unconfigured",
                "missing": ["server_url", "username", "password"],
            }
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        base = self._config["server_url"].rstrip("/")
        token = message.target
        url = f"{base}/ocs/v2.php/apps/spreed/api/v1/chat/{token}"
        headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
        auth = (self._config["username"], self._config["password"])
        payload = {"message": message.text}

        async with httpx.AsyncClient(timeout=15.0, auth=auth) as client:
            response = await client.post(url, headers=headers, data=payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["NextcloudTalkAdapter"]
