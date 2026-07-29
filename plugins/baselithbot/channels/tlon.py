"""Tlon (Urbit) chat adapter via Tlon HTTP gateway.

Tlon does not publish a stable public REST API today; this adapter targets a
configured ``gateway_url`` (operator-supplied bridge such as ``urbit-http`` or
``arvo-bridge``) that accepts ``{target, text}`` JSON envelopes.
"""

from __future__ import annotations

from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage


class TlonAdapter(ChannelAdapter):
    """POST chat messages to a Tlon/Urbit gateway."""

    name = "tlon"
    requires_credentials = ("gateway_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["gateway_url"]}
        payload = {
            "target": message.target,
            "text": message.text,
            "metadata": message.metadata,
        }
        headers = {}
        if "auth_token" in self._config:
            headers["Authorization"] = f"Bearer {self._config['auth_token']}"

        return await self._deliver_via_pool(
            self._config["gateway_url"], json=payload, headers=headers
        )


__all__ = ["TlonAdapter"]
