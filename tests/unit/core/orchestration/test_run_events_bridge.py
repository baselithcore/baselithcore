"""Cross-replica run-event delivery via the Redis bridge.

Without the bridge, run-event fan-out is per-process: an SSE client on
``GET /runs/{id}/events`` only sees events when its HTTP connection lands on
the replica executing the run — broken behind the default 2+-replica HPA.
The bridge routes every published event through Redis pub/sub and re-injects
it into each replica's local stream, so any replica can serve any run's feed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from core.api.events import AgentEvent, EventType
from core.orchestration.run_events import (
    get_run_event_stream,
    publish_run_event,
    reset_run_event_stream,
    set_run_event_broadcaster,
)
from core.orchestration.run_events_bridge import (
    RUN_EVENTS_CHANNEL_PREFIX,
    RedisRunEventsBridge,
)


@pytest.fixture(autouse=True)
def _clean_stream():
    reset_run_event_stream()
    set_run_event_broadcaster(None)
    yield
    set_run_event_broadcaster(None)
    reset_run_event_stream()


class _FakePubSub:
    """Minimal redis pubsub double: pattern-subscribe + listen from a queue."""

    def __init__(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        self.patterns: list[str] = []

    async def psubscribe(self, *patterns: str) -> None:
        self.patterns.extend(patterns)

    async def listen(self):
        while True:
            yield await self._queue.get()

    async def aclose(self) -> None:  # pragma: no cover - teardown path
        return None


class _FakeRedis:
    """Publish routes straight into the pubsub queue (single-process double)."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        await self._queue.put({"type": "pmessage", "channel": channel, "data": payload})
        return 1

    def pubsub(self) -> _FakePubSub:
        return _FakePubSub(self._queue)

    async def aclose(self) -> None:  # pragma: no cover - teardown path
        return None


def test_broadcaster_intercepts_local_publish():
    seen: list[tuple[str, AgentEvent]] = []
    set_run_event_broadcaster(lambda run_id, event: seen.append((run_id, event)))

    subscription = get_run_event_stream().subscribe("r1")
    delivered = publish_run_event("r1", EventType.RUN_STARTED, {"intent": "x"})

    assert len(seen) == 1
    assert seen[0][0] == "r1"
    # Delivery ownership moved to the broadcaster: no direct local fan-out.
    assert delivered == 0
    assert subscription._queue.empty()
    subscription.close()


def test_broadcaster_failure_falls_back_to_local_fanout():
    def broken(run_id: str, event: AgentEvent) -> None:
        raise ConnectionError("redis down")

    set_run_event_broadcaster(broken)
    subscription = get_run_event_stream().subscribe("r1")

    delivered = publish_run_event("r1", EventType.RUN_STARTED)

    assert delivered == 1
    assert subscription._queue.qsize() == 1
    subscription.close()


@pytest.mark.asyncio
async def test_bridge_round_trip_reinjects_into_local_stream():
    fake = _FakeRedis()
    bridge = RedisRunEventsBridge(publisher=fake, subscriber=fake)
    await bridge.start()
    try:
        async with get_run_event_stream().subscribe("run-42") as subscription:
            publish_run_event("run-42", EventType.TOOL_CALL, {"tool_name": "search"})

            event = await asyncio.wait_for(subscription.__anext__(), timeout=2.0)

        assert event.type is EventType.TOOL_CALL
        assert event.data["run_id"] == "run-42"
        assert event.data["tool_name"] == "search"
        # The event travelled through the (fake) Redis channel.
        assert fake.published
        channel, payload = fake.published[0]
        assert channel == f"{RUN_EVENTS_CHANNEL_PREFIX}run-42"
        assert json.loads(payload)["type"] == "tool_call"
    finally:
        await bridge.stop()


@pytest.mark.asyncio
async def test_bridge_stop_restores_local_fanout():
    fake = _FakeRedis()
    bridge = RedisRunEventsBridge(publisher=fake, subscriber=fake)
    await bridge.start()
    await bridge.stop()

    subscription = get_run_event_stream().subscribe("r1")
    delivered = publish_run_event("r1", EventType.RUN_STARTED)

    assert delivered == 1
    assert not fake.published or len(fake.published) == 0
    subscription.close()


def test_agent_event_survives_json_round_trip():
    original = AgentEvent(
        type=EventType.TOOL_RESULT, data={"run_id": "r", "replayed": True}
    )
    restored = AgentEvent.model_validate_json(original.model_dump_json())
    assert restored.type is EventType.TOOL_RESULT
    assert restored.data == original.data


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, run_id: str, event: AgentEvent) -> None:
        self.calls.append((run_id, event))


def test_no_broadcaster_keeps_existing_behavior():
    subscription = get_run_event_stream().subscribe("r1")
    delivered = publish_run_event("r1", EventType.RUN_STARTED)
    assert delivered == 1
    assert subscription._queue.qsize() == 1
    subscription.close()
