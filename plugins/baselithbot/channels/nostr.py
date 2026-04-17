"""Nostr adapter that publishes a kind-1 note via a configured relay over WS."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from .base import ChannelAdapter, ChannelMessage


def _serialize_event(pubkey: str, created_at: int, content: str) -> str:
    arr = [0, pubkey, created_at, 1, [], content]
    return json.dumps(arr, separators=(",", ":"), ensure_ascii=False)


class NostrAdapter(ChannelAdapter):
    """Publish a kind-1 note via a relay WebSocket. Requires ``websockets``."""

    name = "nostr"
    requires_credentials = ("relay_url", "private_key_hex", "public_key_hex")

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {
                "status": "unconfigured",
                "missing": ["relay_url", "private_key_hex", "public_key_hex"],
            }
        try:
            import websockets  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "websockets not installed"}

        try:
            from coincurve import PrivateKey  # type: ignore[import-not-found]
        except ImportError:
            return {
                "status": "error",
                "error": "coincurve not installed (required for Nostr signatures)",
            }

        created_at = int(time.time())
        pubkey = self._config["public_key_hex"]
        serialized = _serialize_event(pubkey, created_at, message.text)
        event_id = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        sig = (
            PrivateKey(bytes.fromhex(self._config["private_key_hex"]))
            .sign_schnorr(bytes.fromhex(event_id))
            .hex()
        )
        event = {
            "id": event_id,
            "pubkey": pubkey,
            "created_at": created_at,
            "kind": 1,
            "tags": [],
            "content": message.text,
            "sig": sig,
        }
        payload = json.dumps(["EVENT", event])

        try:
            async with websockets.connect(self._config["relay_url"]) as ws:
                await ws.send(payload)
                ack = await ws.recv()
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        return {
            "status": "success",
            "channel": self.name,
            "event_id": event_id,
            "ack": str(ack)[:200],
        }


__all__ = ["NostrAdapter"]
