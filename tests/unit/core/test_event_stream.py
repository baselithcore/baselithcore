"""Consumer-group semantics of the durable event stream.

``InMemoryEventStream`` is not a stub — it is the contract's reference
implementation, and a consumer written against it must behave the same on
Redis. These tests pin the properties that make that true: one record to one
member, pending until acknowledged, reclaimable when it is not.
"""

import asyncio

import pytest

from core.events.stream import (
    InMemoryEventStream,
    StreamRecord,
    decode_event_fields,
    encode_event_fields,
)
from core.events.types import Event


def _event(name: str = "order.paid", **data) -> Event:
    return Event(name=name, data=data or {"order_id": 7})


class TestEncoding:
    def test_round_trip_preserves_the_event(self):
        original = Event(
            name="order.paid",
            data={"order_id": 7, "nested": {"a": 1}},
            source="checkout",
            correlation_id="corr-1",
        )
        event, tenant = decode_event_fields(encode_event_fields(original, "acme"))
        assert event.name == original.name
        assert event.data == original.data
        assert event.source == "checkout"
        assert event.correlation_id == "corr-1"
        assert event.timestamp == pytest.approx(original.timestamp)
        assert tenant == "acme"

    def test_unserialisable_payload_is_recorded_not_dropped(self):
        class Opaque:
            def __repr__(self) -> str:
                return "<opaque>"

        fields = encode_event_fields(Event(name="e", data={"x": Opaque()}), "default")
        event, _ = decode_event_fields(fields)
        assert event.data["x"] == "<opaque>"

    def test_malformed_payload_decodes_to_an_empty_dict(self):
        """One bad entry must not be able to stall a whole consumer group."""
        event, tenant = decode_event_fields({"name": "e", "data": "not json"})
        assert event.data == {}
        assert tenant == "default"

    def test_non_object_payload_is_wrapped(self):
        event, _ = decode_event_fields({"name": "e", "data": "[1, 2]"})
        assert event.data == {"value": [1, 2]}

    def test_missing_timestamp_does_not_produce_epoch_zero(self):
        event, _ = decode_event_fields({"name": "e", "data": "{}"})
        assert event.timestamp > 0


class TestDelivery:
    async def test_a_new_group_does_not_replay_history(self):
        """``XGROUP CREATE ... $`` semantics: start at the end of the log."""
        stream = InMemoryEventStream()
        await stream.append(_event())
        await stream.ensure_group("late")
        assert await stream.read("late", "c1", block_ms=0) == []

    async def test_records_appended_after_the_group_are_delivered(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        records = await stream.read("g", "c1", block_ms=0)
        assert [r.event.name for r in records] == ["order.paid"]

    async def test_one_record_goes_to_exactly_one_member(self):
        """The property that makes a group different from a fan-out."""
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        first = await stream.read("g", "c1", block_ms=0)
        second = await stream.read("g", "c2", block_ms=0)
        assert len(first) == 1
        assert second == []

    async def test_separate_groups_each_see_every_record(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("projections")
        await stream.ensure_group("audit")
        await stream.append(_event())
        assert len(await stream.read("projections", "c1", block_ms=0)) == 1
        assert len(await stream.read("audit", "c1", block_ms=0)) == 1

    async def test_count_bounds_a_batch(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        for _ in range(5):
            await stream.append(_event())
        assert len(await stream.read("g", "c1", count=2, block_ms=0)) == 2

    async def test_a_blocking_read_wakes_on_an_append(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")

        async def publish_soon():
            await asyncio.sleep(0.01)
            await stream.append(_event())

        task = asyncio.create_task(publish_soon())
        records = await stream.read("g", "c1", block_ms=2000)
        await task
        assert len(records) == 1

    async def test_a_blocking_read_gives_up(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        assert await stream.read("g", "c1", block_ms=10) == []


class TestPendingAndReclaim:
    async def test_a_delivered_record_stays_pending_until_acked(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        [record] = await stream.read("g", "c1", block_ms=0)
        assert stream.pending_count("g") == 1
        assert await stream.ack("g", record.id) == 1
        assert stream.pending_count("g") == 0

    async def test_acking_an_unknown_id_reports_zero(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        assert await stream.ack("g", "9999-1") == 0

    async def test_an_abandoned_record_is_reclaimed_by_a_live_member(self):
        """A consumer that dies must not take the record with it."""
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        [original] = await stream.read("g", "dead-pod", block_ms=0)

        reclaimed = await stream.reclaim("g", "live-pod", min_idle_ms=0)
        assert [r.id for r in reclaimed] == [original.id]
        assert reclaimed[0].delivery_count == 2

    async def test_reclaim_leaves_recently_claimed_records_alone(self):
        """Never steal work from a consumer that is still doing it."""
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        await stream.read("g", "busy-pod", block_ms=0)
        assert await stream.reclaim("g", "other", min_idle_ms=60_000) == []

    async def test_an_acked_record_is_never_reclaimed(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        [record] = await stream.read("g", "c1", block_ms=0)
        await stream.ack("g", record.id)
        assert await stream.reclaim("g", "c2", min_idle_ms=0) == []

    async def test_delivery_count_grows_with_each_reclaim(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("g")
        await stream.append(_event())
        await stream.read("g", "c1", block_ms=0)
        counts = [
            (await stream.reclaim("g", f"c{n}", min_idle_ms=0))[0].delivery_count
            for n in range(2, 5)
        ]
        assert counts == [2, 3, 4]


class TestBounds:
    async def test_the_log_is_bounded(self):
        """A redelivery buffer, not an archive."""
        stream = InMemoryEventStream(maxlen=3)
        await stream.ensure_group("g")
        for index in range(10):
            await stream.append(_event(order=index))
        records = await stream.read("g", "c1", count=100, block_ms=0)
        assert len(records) == 3

    def test_record_ids_sort_like_redis_ids(self):
        stream = InMemoryEventStream()
        ids = [stream._next_id() for _ in range(3)]
        assert [int(i.rsplit("-", 1)[1]) for i in ids] == [1, 2, 3]

    def test_stream_record_is_immutable(self):
        record = StreamRecord(id="1-1", event=_event())
        with pytest.raises(Exception):
            record.id = "2-2"  # type: ignore[misc]
