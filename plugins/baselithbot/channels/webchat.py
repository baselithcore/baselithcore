"""In-memory WebChat adapter used by the bundled web UI."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from plugins.baselithbot.channels.base import ChannelAdapter, ChannelMessage, ChannelStatus

_DEFAULT_BACKLOG = 200


class WebChatAdapter(ChannelAdapter):
    """In-process queue serving the embedded web chat surface."""

    name = "webchat"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        backlog = int(self._config.get("backlog", _DEFAULT_BACKLOG))
        self._messages: deque[dict[str, Any]] = deque(maxlen=backlog)
        self._wakeup = asyncio.Event()

    def is_configured(self) -> bool:
        return True

    async def startup(self) -> None:
        self._status = ChannelStatus.READY

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        entry = {
            "ts": time.time(),
            "target": message.target,
            "text": message.text,
            "metadata": message.metadata,
        }
        self._messages.append(entry)
        self._wakeup.set()
        return {"status": "success", "channel": self.name, "delivered_at": entry["ts"]}

    async def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._messages)[-limit:]

    async def wait_for_message(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._wakeup.wait(), timeout)
            self._wakeup.clear()
            return True
        except TimeoutError:
            return False


__all__ = ["WebChatAdapter"]
