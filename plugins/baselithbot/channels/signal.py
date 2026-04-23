"""Signal adapter via the signal-cli REST/JSON-RPC bridge."""

from __future__ import annotations

import time
from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class SignalAdapter(ChannelAdapter):
    """Send messages through a local ``signal-cli`` JSON-RPC daemon."""

    name = "signal"
    requires_credentials = ("rpc_url", "from_number")

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "status": "unconfigured",
                "missing": ["rpc_url", "from_number"],
            }
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        url = self._config["rpc_url"]
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "send",
            "params": {
                "account": self._config["from_number"],
                "recipient": [message.target],
                "message": message.text,
            },
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, json=rpc_payload)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["SignalAdapter"]
