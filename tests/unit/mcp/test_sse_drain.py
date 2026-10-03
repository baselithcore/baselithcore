"""An MCP SSE stream ends on the drain signal instead of at the graceful timeout.

A ``subscriptions/listen`` stream only ends when the client leaves, so a
rolling restart would otherwise wait out ``--timeout-graceful-shutdown`` and
then cancel it. The stream races its queue against
:func:`core.lifecycle.drain.wait_for_drain`, flushes what is already queued,
and returns.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from core.lifecycle import drain
from core.mcp.sse import SSEStream, encode_event


@pytest.fixture(autouse=True)
def _fresh_drain_state() -> Iterator[None]:
    drain._reset_for_tests()
    yield
    drain._reset_for_tests()


async def _collect(stream: SSEStream) -> list[str]:
    return [frame async for frame in stream]


async def test_drain_ends_an_idle_stream_promptly() -> None:
    stream = SSEStream(keepalive_seconds=30)
    reader = asyncio.create_task(_collect(stream))
    await asyncio.sleep(0.01)
    drain.mark_draining()
    frames = await asyncio.wait_for(reader, timeout=1.0)
    assert frames == []


async def test_queued_messages_are_flushed_before_the_stream_ends() -> None:
    stream = SSEStream(keepalive_seconds=30)
    await stream.send({"id": 1})
    await stream.send({"id": 2})
    drain.mark_draining()
    frames = await asyncio.wait_for(_collect(stream), timeout=1.0)
    assert frames == [encode_event({"id": 1}), encode_event({"id": 2})]


async def test_messages_and_keepalives_flow_until_close() -> None:
    stream = SSEStream(keepalive_seconds=0.02)
    reader = asyncio.create_task(_collect(stream))
    await asyncio.sleep(0.05)
    await stream.send({"id": 1})
    await stream.close()
    frames = await asyncio.wait_for(reader, timeout=1.0)
    assert ":\n\n" in frames
    assert frames[-1] == encode_event({"id": 1})


async def test_no_pending_getter_is_left_on_the_queue() -> None:
    stream = SSEStream(keepalive_seconds=0.01)
    reader = asyncio.create_task(_collect(stream))
    await asyncio.sleep(0.05)  # several keepalive rounds
    await stream.send({"id": 1})
    await stream.close()
    frames = await asyncio.wait_for(reader, timeout=1.0)
    assert frames.count(encode_event({"id": 1})) == 1


async def test_a_busy_stream_also_ends_on_drain() -> None:
    stream = SSEStream(keepalive_seconds=30)
    frames: list[str] = []

    async def producer() -> None:
        for i in range(10_000):
            await stream.send({"id": i})
            await asyncio.sleep(0)

    feeder = asyncio.create_task(producer())

    async def reader() -> None:
        async for frame in stream:
            frames.append(frame)
            if len(frames) == 5:
                drain.mark_draining()

    await asyncio.wait_for(reader(), timeout=1.0)
    feeder.cancel()
    assert 5 <= len(frames) < 10_000
