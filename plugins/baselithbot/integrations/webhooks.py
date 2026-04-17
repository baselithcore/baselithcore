"""Generic outbound webhook dispatcher with subscription registry."""

from __future__ import annotations

import asyncio
import time
from typing import Any, List

from pydantic import BaseModel, Field


class WebhookSubscription(BaseModel):
    name: str
    url: str
    secret: str | None = None
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)


class WebhookDispatcher:
    """Fan-out events to registered webhook subscriptions."""

    def __init__(self) -> None:
        self._subs: dict[str, WebhookSubscription] = {}

    def subscribe(self, sub: WebhookSubscription) -> None:
        self._subs[sub.name] = sub

    def unsubscribe(self, name: str) -> bool:
        return self._subs.pop(name, None) is not None

    def list(self) -> List[WebhookSubscription]:
        return [s for s in self._subs.values()]

    async def dispatch(self, event: dict[str, Any]) -> List[dict[str, Any]]:
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return [{"status": "error", "error": "httpx not installed"}]

        async def _post(sub: WebhookSubscription) -> dict[str, Any]:
            headers = dict(sub.headers)
            if sub.secret:
                headers.setdefault("X-Baselithbot-Secret", sub.secret)
            async with httpx.AsyncClient(timeout=15.0) as client:
                payload = {"event": event, "ts": time.time(), "subscription": sub.name}
                resp = await client.post(sub.url, json=payload, headers=headers)
            return {
                "subscription": sub.name,
                "status": "success" if resp.is_success else "failed",
                "http_status": resp.status_code,
            }

        results = await asyncio.gather(
            *[_post(s) for s in self._subs.values() if s.enabled],
            return_exceptions=True,
        )
        return [
            r if isinstance(r, dict) else {"status": "error", "error": str(r)}
            for r in results
        ]


__all__ = ["WebhookDispatcher", "WebhookSubscription"]
