"""Matrix adapter via the matrix-nio HTTP client."""

from __future__ import annotations

import time
from typing import Any

from .base import ChannelAdapter, ChannelMessage


class MatrixAdapter(ChannelAdapter):
    """Send messages to a Matrix room using a homeserver access token."""

    name = "matrix"
    requires_credentials = ("homeserver", "access_token")

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["homeserver", "access_token"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        homeserver = self._config["homeserver"].rstrip("/")
        token = self._config["access_token"]
        room_id = message.target
        msgtype = message.metadata.get("msgtype", "m.text")
        txn = str(int(message.metadata.get("txn", 0)) or int(time.time() * 1000))

        url = (
            f"{homeserver}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{txn}"
        )
        payload = {"msgtype": msgtype, "body": message.text}
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.put(url, json=payload, headers=headers)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["MatrixAdapter"]
