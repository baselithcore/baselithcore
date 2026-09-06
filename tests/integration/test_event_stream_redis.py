"""Integration test: the durable event stream against a real Redis.

Runs only against a reachable Redis, and only with the real-service opt-in::

    docker compose up -d redis
    BASELITH_TEST_REAL_REDIS=1 python -m pytest tests/integration/test_event_stream_redis.py

The unit tests assert which commands the backend issues. What they cannot
decide is whether Redis *agrees* with the semantics the code assumes: that
``XREADGROUP >`` hands one record to exactly one member, that an unacknowledged
entry stays pending, that ``XAUTOCLAIM`` reassigns it, and that the reply shapes
the parser indexes into are the ones this server actually returns. A stub
agrees with whatever the code does; a server does not.

Each case uses its own stream key so a failure cannot poison the next, and
deletes it afterwards.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest

from core.events.stream_redis import RedisEventStream
from core.events.types import Event

pytestmark = [pytest.mark.integration]

REDIS_URL = os.environ.get("BASELITH_TEST_REDIS_URL", "redis://localhost:6379/0")
GROUP = "itest"


def _enabled() -> bool:
    return os.environ.get("BASELITH_TEST_REAL_REDIS") == "1"


@pytest.fixture
async def stream() -> AsyncIterator[RedisEventStream]:
    """A stream on its own key, against a live server."""
    if not _enabled():
        pytest.skip("set BASELITH_TEST_REAL_REDIS=1 (and run a Redis) to enable")
    try:
        from redis.asyncio import Redis
    except ImportError:  # pragma: no cover - redis extra not installed
        pytest.skip("redis package not installed")

    client = Redis.from_url(REDIS_URL, decode_responses=False)
    try:
        await client.ping()
    except Exception as exc:  # pragma: no cover - server not reachable
        await client.aclose()
        pytest.skip(f"Redis not reachable at {REDIS_URL}: {exc}")

    key = f"itest:events:{uuid.uuid4().hex[:8]}"
    try:
        yield RedisEventStream(client, key=key, maxlen=100)
    finally:
        await client.delete(key)
        await client.aclose()


def _event(name: str = "order.paid", **data) -> Event:
    return Event(name=name, data=data or {"order_id": 7}, source="itest")


class TestGroupCreation:
    async def test_the_group_is_created_and_recreating_is_a_no_op(self, stream):
        """``BUSYGROUP`` is the success case for an idempotent ensure_group."""
        await stream.ensure_group(GROUP)
        await stream.ensure_group(GROUP)  # must not raise

    async def test_a_new_group_does_not_replay_history(self, stream):
        """``XGROUP CREATE ... $``: a replica coming up must not replay."""
        await stream.append(_event())
        await stream.ensure_group(GROUP)
        assert await stream.read(GROUP, "c1", block_ms=0) == []


class TestDelivery:
    async def test_a_record_round_trips_through_redis(self, stream):
        await stream.ensure_group(GROUP)
        record_id = await stream.append(
            Event(
                name="order.paid",
                data={"order_id": 7, "nested": {"a": 1}},
                source="checkout",
                correlation_id="corr-1",
            ),
            tenant_id="acme",
        )

        [record] = await stream.read(GROUP, "c1", block_ms=0)
        assert record.id == record_id
        assert record.event.name == "order.paid"
        assert record.event.data == {"order_id": 7, "nested": {"a": 1}}
        assert record.event.source == "checkout"
        assert record.event.correlation_id == "corr-1"
        assert record.tenant_id == "acme"
        assert record.event.timestamp > 0

    async def test_one_record_goes_to_exactly_one_member(self, stream):
        """The property that separates a consumer group from a fan-out."""
        await stream.ensure_group(GROUP)
        await stream.append(_event())

        first = await stream.read(GROUP, "pod-a", block_ms=0)
        second = await stream.read(GROUP, "pod-b", block_ms=0)
        assert len(first) == 1
        assert second == []

    async def test_separate_groups_each_see_every_record(self, stream):
        await stream.ensure_group("projections")
        await stream.ensure_group("audit")
        await stream.append(_event())
        assert len(await stream.read("projections", "c1", block_ms=0)) == 1
        assert len(await stream.read("audit", "c1", block_ms=0)) == 1

    async def test_count_bounds_a_batch(self, stream):
        await stream.ensure_group(GROUP)
        for index in range(5):
            await stream.append(_event(order=index))
        assert len(await stream.read(GROUP, "c1", count=2, block_ms=0)) == 2

    async def test_a_blocking_read_gives_up(self, stream):
        await stream.ensure_group(GROUP)
        assert await stream.read(GROUP, "c1", block_ms=50) == []


class TestPendingAndReclaim:
    async def test_a_delivered_record_stays_pending_until_acked(self, stream):
        await stream.ensure_group(GROUP)
        await stream.append(_event())
        [record] = await stream.read(GROUP, "c1", block_ms=0)

        assert await stream.pending_count(GROUP) == 1
        assert await stream.ack(GROUP, record.id) == 1
        assert await stream.pending_count(GROUP) == 0

    async def test_an_abandoned_record_is_reclaimed_by_a_live_member(self, stream):
        """The crash-recovery path: nothing else in Redis reassigns entries."""
        await stream.ensure_group(GROUP)
        await stream.append(_event())
        [original] = await stream.read(GROUP, "dead-pod", block_ms=0)

        reclaimed = await stream.reclaim(GROUP, "live-pod", min_idle_ms=0)
        assert [r.id for r in reclaimed] == [original.id]
        assert reclaimed[0].delivery_count > 1
        assert reclaimed[0].event.data == original.event.data

    async def test_reclaim_leaves_recently_claimed_records_alone(self, stream):
        """Never steal work from a consumer that is still doing it."""
        await stream.ensure_group(GROUP)
        await stream.append(_event())
        await stream.read(GROUP, "busy-pod", block_ms=0)
        assert await stream.reclaim(GROUP, "other", min_idle_ms=60_000) == []

    async def test_an_acked_record_is_never_reclaimed(self, stream):
        await stream.ensure_group(GROUP)
        await stream.append(_event())
        [record] = await stream.read(GROUP, "c1", block_ms=0)
        await stream.ack(GROUP, record.id)
        assert await stream.reclaim(GROUP, "c2", min_idle_ms=0) == []

    async def test_a_deleted_entry_is_acked_rather_than_reclaimed_forever(self, stream):
        """A tombstone has no event but still occupies the pending list."""
        await stream.ensure_group(GROUP)
        await stream.append(_event())
        [record] = await stream.read(GROUP, "dead-pod", block_ms=0)
        await stream._client.xdel(stream.key, record.id)

        assert await stream.reclaim(GROUP, "live-pod", min_idle_ms=0) == []
        assert await stream.pending_count(GROUP) == 0


class TestBridgeOverRedis:
    async def test_the_bridge_consumes_what_it_published(self, stream):
        """The end-to-end path a second replica would take."""
        import asyncio

        from core.events.bus import EventBus
        from core.events.durable import DurableEventBridge

        seen: list[dict] = []
        arrived = asyncio.Event()
        bus = EventBus()

        def handler(data):
            seen.append(data)
            arrived.set()

        bus.subscribe("order.paid", handler)
        bridge = DurableEventBridge(
            stream, bus=bus, group=GROUP, consumer="pod-1", block_ms=50
        )

        await bridge.start()
        try:
            await bridge.publish("order.paid", {"order_id": 7})
            await asyncio.wait_for(arrived.wait(), timeout=5)
        finally:
            await bridge.stop(timeout=5)

        # Published once, handled once locally and once off the stream: the
        # bridge emits on both paths by design, which is why handlers must be
        # idempotent. What matters here is that the stream path worked at all.
        assert {"order_id": 7} in seen
        assert bridge.stats.consumed >= 1
        assert await stream.pending_count(GROUP) == 0
