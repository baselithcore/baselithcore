"""The startup Redis ping is bounded: an unreachable Redis fails fast."""

from __future__ import annotations

import asyncio
import socket
import time
from typing import Any

import pytest

from core.api import startup_checks


async def test_ping_passes_socket_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Client:
        async def ping(self) -> bool:
            return True

        async def aclose(self) -> None:
            seen["closed"] = True

    def _from_url(url: str, **kwargs: Any) -> _Client:
        seen.update(kwargs)
        return _Client()

    monkeypatch.setattr(startup_checks.redis, "from_url", _from_url)
    await startup_checks._ping_redis("redis://cache:6379/1")
    assert (
        seen["socket_connect_timeout"] == startup_checks.STARTUP_REDIS_CONNECT_TIMEOUT_S
    )
    assert seen["socket_timeout"] == startup_checks.STARTUP_REDIS_SOCKET_TIMEOUT_S
    assert seen["closed"] is True


async def test_silent_redis_is_abandoned_within_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that accepts the TCP connection and never answers."""
    monkeypatch.setattr(startup_checks, "STARTUP_REDIS_SOCKET_TIMEOUT_S", 0.2)
    monkeypatch.setattr(startup_checks, "STARTUP_REDIS_PROBE_TIMEOUT_S", 1.0)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    try:
        started = time.monotonic()
        with pytest.raises((TimeoutError, asyncio.TimeoutError, Exception)):
            await startup_checks._ping_redis(f"redis://127.0.0.1:{port}/0")
        assert time.monotonic() - started < 3.0
    finally:
        listener.close()
