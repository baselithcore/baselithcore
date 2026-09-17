"""A subscription must outlive the pool's read deadline, and give its connection back.

The shared Redis pool sets ``socket_timeout`` so a silent server cannot hang a
caller forever (``core.cache.redis_cache.create_redis_client``). redis-py
applies that deadline to *every* read with no explicit timeout of its own —
including the blocking read behind ``PubSub.listen()`` — so an idle subscriber
used to die every ``socket_timeout`` seconds with
``TimeoutError: Timeout reading from ...`` and its connection was left checked
out of the bounded pool. These tests pin the two halves of the fix.
"""

from __future__ import annotations

import asyncio

import pytest

from core.realtime.subscriptions import close_pubsub, iter_messages


class _FakePubSub:
    """redis-py's contract for the two read paths, including the deadline.

    ``listen()`` reads with no timeout of its own, so it inherits the
    connection's ``socket_timeout`` and *raises*; ``get_message(timeout=t)``
    passes its own deadline down and returns ``None`` without disconnecting.
    """

    socket_timeout = 0.05

    def __init__(self, feed: list[dict] | None = None) -> None:
        self._feed = list(feed or [])
        self.closed = False
        self.idle_polls = 0

    async def listen(self):
        for message in self._feed:
            yield message
        await asyncio.sleep(self.socket_timeout)
        raise TimeoutError("Timeout reading from fake:6379")

    async def get_message(self, *, ignore_subscribe_messages=False, timeout=0.0):
        if self._feed:
            return self._feed.pop(0)
        self.idle_polls += 1
        await asyncio.sleep(min(timeout, self.socket_timeout))
        return None

    async def aclose(self) -> None:
        self.closed = True


async def _drain(pubsub: _FakePubSub, *, count: int, idle_timeout: float) -> list[dict]:
    seen: list[dict] = []
    async for message in iter_messages(pubsub, idle_timeout=idle_timeout):
        seen.append(message)
        if len(seen) == count:
            break
    return seen


async def test_listen_dies_on_the_pool_read_deadline() -> None:
    """The shape being replaced: an idle ``listen()`` raises, losing the sub."""
    pubsub = _FakePubSub()
    with pytest.raises(TimeoutError):
        async for _ in pubsub.listen():
            pass


async def test_idle_subscription_survives_the_read_deadline() -> None:
    """Polling with an explicit timeout keeps the subscription across idle gaps."""
    pubsub = _FakePubSub()

    async def publish_late() -> None:
        await asyncio.sleep(0.3)  # several deadlines' worth of silence
        pubsub._feed.append({"type": "message", "data": "late"})

    task = asyncio.create_task(publish_late())
    seen = await asyncio.wait_for(
        _drain(pubsub, count=1, idle_timeout=0.02), timeout=5.0
    )
    await task

    assert seen == [{"type": "message", "data": "late"}]
    assert pubsub.idle_polls >= 2, "the loop never idled, so nothing was proven"


async def test_messages_are_yielded_in_order() -> None:
    feed = [{"type": "message", "data": str(i)} for i in range(3)]
    pubsub = _FakePubSub(feed=list(feed))

    seen = await asyncio.wait_for(
        _drain(pubsub, count=3, idle_timeout=0.02), timeout=5.0
    )

    assert seen == feed


async def test_close_pubsub_releases_and_never_raises() -> None:
    pubsub = _FakePubSub()
    await close_pubsub(pubsub)
    assert pubsub.closed

    await close_pubsub(None)  # nothing subscribed — a no-op, not a crash

    class _Broken:
        async def aclose(self) -> None:
            raise RuntimeError("connection already gone")

    await close_pubsub(_Broken())  # cleanup must never mask the caller's error
