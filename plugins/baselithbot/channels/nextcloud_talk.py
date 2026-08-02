"""Nextcloud Talk adapter via OCS API."""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


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
        base = self._config["server_url"].rstrip("/")
        token = message.target
        url = f"{base}/ocs/v2.php/apps/spreed/api/v1/chat/{token}"
        headers = {"OCS-APIRequest": "true", "Accept": "application/json"}
        auth = (self._config["username"], self._config["password"])
        payload = {"message": message.text}

        return await self._deliver_via_pool(url, headers=headers, data=payload, auth=auth)


__all__ = ["NextcloudTalkAdapter"]
