"""Twitch chat adapter (IRC-over-WebSocket flavor: TLS TCP socket)."""

from __future__ import annotations

import asyncio
import ssl
from contextlib import suppress
from typing import Any

from .base import ChannelAdapter, ChannelMessage, ChannelStatus


class TwitchAdapter(ChannelAdapter):
    """Send PRIVMSG to a Twitch chat channel via the IRC bridge."""

    name = "twitch"
    requires_credentials = ("oauth_token", "nick")

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._writer: asyncio.StreamWriter | None = None

    async def startup(self) -> None:
        if not self.is_configured():
            self._status = ChannelStatus.UNCONFIGURED
            return
        ctx = ssl.create_default_context()
        _, self._writer = await asyncio.open_connection("irc.chat.twitch.tv", 6697, ssl=ctx)
        token = self._config["oauth_token"]
        nick = self._config["nick"]
        self._writer.write(f"PASS oauth:{token}\r\n".encode())
        self._writer.write(f"NICK {nick}\r\n".encode())
        await self._writer.drain()
        self._status = ChannelStatus.READY

    async def send(self, message: ChannelMessage) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "missing": ["oauth_token", "nick"]}
        if self._writer is None:
            await self.startup()
        if self._writer is None:
            return {"status": "error", "error": "connection failed"}

        target = message.target if message.target.startswith("#") else f"#{message.target}"
        self._writer.write(f"JOIN {target}\r\n".encode())
        self._writer.write(f"PRIVMSG {target} :{message.text}\r\n".encode())
        await self._writer.drain()
        return {"status": "success", "channel": self.name, "target": target}

    async def shutdown(self) -> None:
        if self._writer is not None:
            with suppress(Exception):
                self._writer.write(b"QUIT :baselithbot shutdown\r\n")
                await self._writer.drain()
                self._writer.close()
                await self._writer.wait_closed()
        self._writer = None
        self._status = ChannelStatus.UNCONFIGURED


__all__ = ["TwitchAdapter"]
