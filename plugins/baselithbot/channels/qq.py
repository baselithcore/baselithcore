"""QQ adapter targeting a CQHTTP / OneBot-style local bridge."""

from __future__ import annotations

from typing import Any

from .base import ChannelAdapter, ChannelMessage


class QQAdapter(ChannelAdapter):
    """Send messages via a local OneBot v11 / CQHTTP gateway HTTP endpoint."""

    name = "qq"
    requires_credentials = ("gateway_url",)

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["gateway_url"]}
        try:
            import httpx  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "error": "httpx not installed"}

        base = self._config["gateway_url"].rstrip("/")
        is_group = bool(message.metadata.get("group", False))
        url = f"{base}/{'send_group_msg' if is_group else 'send_private_msg'}"
        payload: dict[str, Any] = {"message": message.text}
        if is_group:
            payload["group_id"] = int(message.target)
        else:
            payload["user_id"] = int(message.target)

        headers = {}
        if "access_token" in self._config:
            headers["Authorization"] = f"Bearer {self._config['access_token']}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers=headers)
        return {
            "status": "success" if response.is_success else "failed",
            "http_status": response.status_code,
            "channel": self.name,
        }


__all__ = ["QQAdapter"]
