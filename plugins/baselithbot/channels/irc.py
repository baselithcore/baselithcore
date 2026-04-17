"""IRC adapter (raw protocol over asyncio TCP)."""

from __future__ import annotations

import asyncio
from typing import Any

from .base import ChannelAdapter, ChannelMessage, ChannelStatus


class IRCAdapter(ChannelAdapter):
    """Connect to an IRC server and send PRIVMSG to a target channel/nick."""

    name = "irc"
    requires_credentials = ("server", "nick")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def startup(self) -> None:
        if not self.is_configured():
            self._status = ChannelStatus.UNCONFIGURED
            return
        host = self._config["server"]
        port = int(self._config.get("port", 6667))
        nick = self._config["nick"]

        self._reader, self._writer = await asyncio.open_connection(host, port)
        self._writer.write(f"NICK {nick}\r\n".encode())
        self._writer.write(f"USER {nick} 0 * :{nick}\r\n".encode())
        await self._writer.drain()
        self._status = ChannelStatus.READY

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["server", "nick"]}
        if self._writer is None:
            await self.startup()
        if self._writer is None:
            return {"status": "error", "error": "connection failed"}

        line = f"PRIVMSG {message.target} :{message.text}\r\n".encode()
        self._writer.write(line)
        await self._writer.drain()
        return {
            "status": "success",
            "channel": self.name,
            "target": message.target,
        }

    async def shutdown(self) -> None:
        if self._writer is not None:
            try:
                self._writer.write(b"QUIT :baselithbot shutdown\r\n")
                await self._writer.drain()
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None
        self._status = ChannelStatus.UNCONFIGURED


__all__ = ["IRCAdapter"]
