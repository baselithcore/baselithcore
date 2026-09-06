"""The bridge that makes bus events survive a crash and cross a replica.

The failure that gets most of the attention here is silent in production: a
record whose handler failed must stay pending and come back, never be quietly
acknowledged and lost.
"""

import asyncio

import pytest

from core.events.bus import EventBus
from core.events.durable import POISON_DELIVERY_THRESHOLD, DurableEventBridge
from core.events.stream import InMemoryEventStream, StreamRecord
from core.events.types import Event


def _bridge(stream=None, bus=None, **kwargs) -> DurableEventBridge:
    return DurableEventBridge(
        stream or InMemoryEventStream(),
        bus=bus or EventBus(),
        group="test",
        consumer="c1",
        **kwargs,
    )


class TestPublish:
    async def test_publish_records_then_emits(self):
        stream = InMemoryEventStream()
        bus = EventBus()
        seen = []
        bus.subscribe("order.paid", lambda data: seen.append(data))

        bridge = _bridge(stream, bus)
        await stream.ensure_group("test")
        record_id = await bridge.publish("order.paid", {"order_id": 7})

        assert record_id
        assert seen == [{"order_id": 7}]
        [record] = await stream.read("test", "c1", block_ms=0)
        assert record.event.data == {"order_id": 7}

    async def test_the_tenant_is_captured_at_publish(self):
        from core.context import reset_tenant_context, set_tenant_context

        stream = InMemoryEventStream()
        await stream.ensure_group("test")
        token = set_tenant_context("acme")
        try:
            await _bridge(stream).publish("order.paid", {})
        finally:
            reset_tenant_context(token)

        [record] = await stream.read("test", "c1", block_ms=0)
        assert record.tenant_id == "acme"

    async def test_source_and_correlation_id_survive_the_log(self):
        stream = InMemoryEventStream()
        await stream.ensure_group("test")
        await _bridge(stream).publish(
            "order.paid", {}, source="checkout", correlation_id="corr-9"
        )
        [record] = await stream.read("test", "c1", block_ms=0)
        assert record.event.source == "checkout"
        assert record.event.correlation_id == "corr-9"


class TestNoFeedbackLoop:
    async def test_dispatching_a_record_does_not_append_it_again(self):
        """Consumption emits on the bus; it must never re-enter the log."""
        stream = InMemoryEventStream()
        await stream.ensure_group("test")
        bridge = _bridge(stream)

        await bridge._dispatch(
            StreamRecord(id="1-1", event=Event(name="order.paid", data={"n": 1}))
        )
        assert await stream.read("test", "c1", block_ms=0) == []

    async def test_an_event_derived_by_a_handler_is_recorded(self):
        """The case a blanket dispatch-time suppression would silently lose."""
        stream = InMemoryEventStream()
        bus = EventBus()
        bridge = _bridge(stream, bus)
        await stream.ensure_group("test")

        async def derive(data):
            await bridge.publish("invoice.created", {"from": data})

        bus.subscribe("order.paid", derive)
        await bridge._dispatch(
            StreamRecord(id="1-1", event=Event(name="order.paid", data={"n": 1}))
        )

        [record] = await stream.read("test", "c1", block_ms=0)
        assert record.event.name == "invoice.created"


class TestDispatch:
    async def test_a_handled_record_is_acknowledged(self):
        stream = InMemoryEventStream()
        bus = EventBus()
        bus.subscribe("order.paid", lambda data: None)
        bridge = _bridge(stream, bus)
        await stream.ensure_group("test")
        await bridge.publish("order.paid", {})
        [record] = await stream.read("test", "c1", block_ms=0)

        await bridge._dispatch(record)

        assert stream.pending_count("test") == 0
        assert bridge.stats.consumed == 1

    async def test_the_emitting_tenant_is_restored_for_handlers(self):
        """A replica has no request context; the record carries the tenant."""
        from core.context import get_tenant_or_default

        seen = []
        bus = EventBus()
        bus.subscribe("order.paid", lambda data: seen.append(get_tenant_or_default()))
        bridge = _bridge(bus=bus)

        await bridge._dispatch(
            StreamRecord(
                id="1-1", event=Event(name="order.paid", data={}), tenant_id="acme"
            )
        )
        assert seen == ["acme"]

    async def test_the_tenant_context_does_not_leak_past_the_dispatch(self):
        from core.context import get_tenant_or_default

        bridge = _bridge()
        await bridge._dispatch(
            StreamRecord(
                id="1-1", event=Event(name="order.paid", data={}), tenant_id="acme"
            )
        )
        assert get_tenant_or_default() == "default"

    async def test_a_failed_dispatch_leaves_the_record_pending(self):
        """Unacknowledged means redelivered — that is the durability claim."""
        stream = InMemoryEventStream()
        bus = EventBus()
        bridge = _bridge(stream, bus)
        await stream.ensure_group("test")
        await bridge.publish("order.paid", {})
        [record] = await stream.read("test", "c1", block_ms=0)

        # The bus swallows handler errors, so failure is injected at the emit.
        async def failing_emit(*args, **kwargs):
            raise RuntimeError("redis went away")

        bus.emit = failing_emit  # type: ignore[method-assign]
        await bridge._dispatch(record)

        assert stream.pending_count("test") == 1
        assert bridge.stats.failed == 1
        assert bridge.stats.consumed == 0

    @pytest.mark.parametrize(
        "deliveries,dropped",
        [(POISON_DELIVERY_THRESHOLD, False), (POISON_DELIVERY_THRESHOLD + 1, True)],
    )
    async def test_a_poison_record_is_eventually_dropped(self, deliveries, dropped):
        """A record that always fails must not block the reclaim path forever."""
        emitted = []
        bus = EventBus()
        bus.subscribe("order.paid", lambda data: emitted.append(data))
        stream = InMemoryEventStream()
        await stream.ensure_group("test")
        bridge = _bridge(stream, bus)

        await bridge._dispatch(
            StreamRecord(
                id="1-1",
                event=Event(name="order.paid", data={}),
                delivery_count=deliveries,
            )
        )
        assert (emitted == []) is dropped


class TestConsumerLifecycle:
    async def test_start_consumes_published_events(self):
        stream = InMemoryEventStream()
        bus = EventBus()
        seen = asyncio.Event()
        bus.subscribe("order.paid", lambda data: seen.set())
        bridge = _bridge(stream, bus, block_ms=20, reclaim_idle_ms=10_000)

        await bridge.start()
        try:
            await stream.append(Event(name="order.paid", data={"n": 1}))
            await asyncio.wait_for(seen.wait(), timeout=2)
        finally:
            await bridge.stop(timeout=2)

        assert bridge.stats.consumed >= 1

    async def test_start_is_idempotent(self):
        bridge = _bridge(block_ms=20)
        await bridge.start()
        first = bridge._task
        await bridge.start()
        try:
            assert bridge._task is first
        finally:
            await bridge.stop(timeout=2)

    async def test_stop_without_start_is_a_no_op(self):
        await _bridge().stop()

    async def test_the_loop_survives_a_stream_error(self):
        """A transient backend failure must not end event processing."""
        calls = {"n": 0}

        class Flaky(InMemoryEventStream):
            async def read(self, *args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError("connection reset")
                return await super().read(*args, **kwargs)

        stream = Flaky()
        bridge = _bridge(stream, block_ms=20, reclaim_idle_ms=10_000)
        await bridge.start()
        try:
            await asyncio.sleep(0.15)
        finally:
            await bridge.stop(timeout=2)

        assert calls["n"] >= 2
        assert bridge.stats.failed >= 1
