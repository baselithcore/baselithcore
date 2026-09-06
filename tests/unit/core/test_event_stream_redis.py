"""The Redis Streams backend, against a stub client.

Redis itself is not under test — the mapping onto its commands is, because
every mistake in it is silent: a wrong ``XREADGROUP`` id replays history, a
swallowed error hides a broken group, an unacknowledged tombstone leaks the
pending list. Behaviour against a real server is covered by the integration
suite.
"""

import pytest

from core.events.stream_redis import DEFAULT_STREAM_KEY, RedisEventStream
from core.events.types import Event


class FakeRedis:
    """Records the commands issued and replays canned answers."""

    def __init__(self, **replies):
        self.calls: list[tuple[str, tuple, dict]] = []
        self.replies = replies

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))
        return self.replies.get(name)

    async def xadd(self, *args, **kwargs):
        self._record("xadd", args, kwargs)
        return self.replies.get("xadd", b"1700000000000-0")

    async def xgroup_create(self, *args, **kwargs):
        self._record("xgroup_create", args, kwargs)
        error = self.replies.get("xgroup_create_error")
        if error is not None:
            raise error

    async def xreadgroup(self, *args, **kwargs):
        return self._record("xreadgroup", args, kwargs)

    async def xack(self, *args, **kwargs):
        self._record("xack", args, kwargs)
        return self.replies.get("xack", len(args) - 2)

    async def xautoclaim(self, *args, **kwargs):
        return self._record("xautoclaim", args, kwargs)

    async def xpending(self, *args, **kwargs):
        return self._record("xpending", args, kwargs)

    def call(self, name):
        return next(c for c in self.calls if c[0] == name)

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)


def _entry(record_id=b"1-1", **fields):
    payload = {
        b"name": b"order.paid",
        b"data": b'{"order_id": 7}',
        b"source": b"",
        b"correlation_id": b"",
        b"timestamp": b"1700000000.0",
        b"tenant_id": b"acme",
    }
    payload.update({k.encode(): v for k, v in fields.items()})
    return (record_id, payload)


class TestAppend:
    async def test_append_trims_approximately(self):
        """Exact trimming walks the log on every write; the bound is a buffer."""
        client = FakeRedis()
        stream = RedisEventStream(client, maxlen=500)
        record_id = await stream.append(Event(name="order.paid", data={"n": 1}))

        _, args, kwargs = client.call("xadd")
        assert args[0] == DEFAULT_STREAM_KEY
        assert args[1]["name"] == "order.paid"
        assert kwargs == {"maxlen": 500, "approximate": True}
        assert record_id == "1700000000000-0"

    async def test_the_tenant_is_written_with_the_event(self):
        client = FakeRedis()
        await RedisEventStream(client).append(
            Event(name="e", data={}), tenant_id="acme"
        )
        assert client.call("xadd")[1][1]["tenant_id"] == "acme"

    async def test_maxlen_is_never_zero(self):
        client = FakeRedis()
        await RedisEventStream(client, maxlen=0).append(Event(name="e", data={}))
        assert client.call("xadd")[2]["maxlen"] == 1


class TestGroupCreation:
    async def test_the_group_starts_at_the_end_of_the_log(self):
        """A replica coming up must not replay the deployment's history."""
        client = FakeRedis()
        await RedisEventStream(client).ensure_group("g")
        _, args, kwargs = client.call("xgroup_create")
        assert args[1] == "g"
        assert kwargs == {"id": "$", "mkstream": True}

    async def test_an_existing_group_is_not_an_error(self):
        client = FakeRedis(
            xgroup_create_error=RuntimeError("BUSYGROUP Consumer Group name...")
        )
        await RedisEventStream(client).ensure_group("g")  # must not raise

    async def test_any_other_error_propagates(self):
        """A broken Redis must not look like a healthy idempotent create."""
        client = FakeRedis(xgroup_create_error=RuntimeError("NOAUTH"))
        with pytest.raises(RuntimeError, match="NOAUTH"):
            await RedisEventStream(client).ensure_group("g")


class TestRead:
    async def test_read_asks_only_for_undelivered_records(self):
        client = FakeRedis(xreadgroup=[(b"stream", [_entry()])])
        records = await RedisEventStream(client).read("g", "c1", count=5, block_ms=250)

        _, args, kwargs = client.call("xreadgroup")
        assert args[:2] == ("g", "c1")
        assert args[2] == {DEFAULT_STREAM_KEY: ">"}
        assert kwargs == {"count": 5, "block": 250}
        assert [r.event.data for r in records] == [{"order_id": 7}]
        assert records[0].tenant_id == "acme"
        assert records[0].delivery_count == 1

    async def test_a_non_blocking_read_passes_no_block(self):
        client = FakeRedis(xreadgroup=[])
        await RedisEventStream(client).read("g", "c1", block_ms=0)
        assert client.call("xreadgroup")[2]["block"] is None

    async def test_an_empty_reply_is_no_records(self):
        client = FakeRedis(xreadgroup=None)
        assert await RedisEventStream(client).read("g", "c1") == []

    async def test_string_replies_are_handled(self):
        """Clients with decode_responses=True hand back str, not bytes."""
        entry = (
            "1-1",
            {"name": "order.paid", "data": '{"n": 1}', "tenant_id": "acme"},
        )
        client = FakeRedis(xreadgroup=[("stream", [entry])])
        [record] = await RedisEventStream(client).read("g", "c1")
        assert record.id == "1-1"
        assert record.event.data == {"n": 1}


class TestAckAndReclaim:
    async def test_ack_without_ids_never_touches_redis(self):
        client = FakeRedis()
        assert await RedisEventStream(client).ack("g") == 0
        assert client.count("xack") == 0

    async def test_reclaim_takes_idle_entries(self):
        client = FakeRedis(xautoclaim=(b"0-0", [_entry()], []))
        [record] = await RedisEventStream(client).reclaim(
            "g", "live", min_idle_ms=30_000, count=4
        )

        _, args, kwargs = client.call("xautoclaim")
        assert args[1:3] == ("g", "live")
        assert kwargs == {"min_idle_time": 30_000, "count": 4}
        assert record.delivery_count == 2

    async def test_a_redis_6_reply_without_the_deleted_list_is_handled(self):
        client = FakeRedis(xautoclaim=(b"0-0", [_entry()]))
        assert len(await RedisEventStream(client).reclaim("g", "live")) == 1

    async def test_deleted_entries_are_acknowledged_not_returned(self):
        """A tombstone has no event but still occupies the pending list."""
        client = FakeRedis(xautoclaim=(b"0-0", [(b"1-1", None), _entry(b"1-2")], []))
        records = await RedisEventStream(client).reclaim("g", "live")

        assert [r.id for r in records] == ["1-2"]
        assert client.call("xack")[1][2:] == ("1-1",)

    async def test_an_empty_reclaim_issues_no_ack(self):
        client = FakeRedis(xautoclaim=(b"0-0", [], []))
        assert await RedisEventStream(client).reclaim("g", "live") == []
        assert client.count("xack") == 0


class TestPending:
    async def test_dict_reply(self):
        client = FakeRedis(xpending={"pending": 3})
        assert await RedisEventStream(client).pending_count("g") == 3

    async def test_list_reply(self):
        client = FakeRedis(xpending=[2, b"1-1", b"1-2", []])
        assert await RedisEventStream(client).pending_count("g") == 2

    async def test_empty_reply(self):
        client = FakeRedis(xpending=None)
        assert await RedisEventStream(client).pending_count("g") == 0


def test_the_backend_matches_the_protocol_signatures():
    """``RedisEventStream`` must be swappable for the in-memory reference."""
    import inspect

    from core.events.stream import EventStream, InMemoryEventStream

    for name in ("append", "ensure_group", "read", "ack", "reclaim"):
        expected = inspect.signature(getattr(EventStream, name))
        for implementation in (RedisEventStream, InMemoryEventStream):
            assert inspect.signature(getattr(implementation, name)) == expected, (
                f"{implementation.__name__}.{name} diverged from the protocol"
            )
