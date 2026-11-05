"""A queue worker hosts no plugins, so usage sinks never run there.

The outbox hands what a job billed to the process that does run sinks: the work
horse collects every billed turn of the job and pushes it to Redis on exit; the
API process drains it and re-emits each turn with the identity the job ran
under, so a sink (a dashboard's per-plugin ledger) sees it as if the call had
been made in-process.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from baselith.core.context import (
    get_current_plugin,
    get_current_tenant_id,
    get_current_user_id,
    reset_plugin_context,
    reset_tenant_context,
    reset_user_context,
    set_plugin_context,
    set_tenant_context,
    set_user_context,
)
from baselith.core.services.llm import (
    UsageReport,
    register_usage_sink,
    unregister_usage_sink,
)
from baselith.core.services.llm import usage_outbox as outbox
from baselith.core.services.llm.usage import Usage
from baselith.core.services.llm.usage_sinks import emit_usage_report


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple[str, tuple[Any, ...]]] = []

    def __enter__(self) -> FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def rpush(self, key: str, *values: str) -> FakePipeline:
        self.ops.append(("rpush", (key, *values)))
        return self

    def ltrim(self, key: str, start: int, stop: int) -> FakePipeline:
        self.ops.append(("ltrim", (key, start, stop)))
        return self

    def expire(self, key: str, seconds: int) -> FakePipeline:
        self.ops.append(("expire", (key, seconds)))
        return self

    def lrange(self, key: str, start: int, stop: int) -> FakePipeline:
        self.ops.append(("lrange", (key, start, stop)))
        return self

    def execute(self) -> list[Any]:
        results: list[Any] = []
        for name, args in self.ops:
            results.append(getattr(self.redis, f"_{name}")(*args))
        self.ops.clear()
        return results


class FakeRedis:
    """The few list commands the outbox uses, with their real semantics."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttl: dict[str, int] = {}
        self.broken = False

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        if self.broken:
            raise ConnectionError("redis down")
        return FakePipeline(self)

    def _rpush(self, key: str, *values: str) -> int:
        self.lists.setdefault(key, []).extend(values)
        return len(self.lists[key])

    def _ltrim(self, key: str, start: int, stop: int) -> bool:
        items = self.lists.get(key, [])
        n = len(items)
        lo = max(n + start, 0) if start < 0 else start
        hi = n + stop if stop < 0 else stop
        self.lists[key] = items[lo : hi + 1]
        return True

    def _expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = seconds
        return True

    def _lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self.lists.get(key, [])
        hi = len(items) + stop if stop < 0 else stop
        return items[start : hi + 1]

    def close(self) -> None:
        return None


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def identity() -> Iterator[None]:
    tokens = (
        set_tenant_context("tenant-a"),
        set_user_context("user-1"),
        set_plugin_context("agent_range"),
    )
    try:
        yield
    finally:
        reset_plugin_context(tokens[2])
        reset_user_context(tokens[1])
        reset_tenant_context(tokens[0])


def billed(model: str = "ollama/qwen", **usage: int) -> UsageReport:
    return UsageReport(
        model=model, usage=Usage(**{"input_tokens": 100, "output_tokens": 50, **usage})
    )


def pushed(redis: FakeRedis) -> list[dict[str, Any]]:
    return [json.loads(raw) for raw in redis.lists.get(outbox.OUTBOX_KEY, [])]


def test_a_billed_turn_inside_a_job_is_pushed_with_the_jobs_identity(
    redis: FakeRedis, identity: None
) -> None:
    with outbox.collect_job_usage(redis):
        emit_usage_report(billed(cache_read_tokens=7, cache_write_tokens=3))
    [record] = pushed(redis)
    assert record["model"] == "ollama/qwen"
    assert (record["input_tokens"], record["output_tokens"]) == (100, 50)
    assert (record["cache_read_tokens"], record["cache_write_tokens"]) == (7, 3)
    assert (record["tenant_id"], record["user_id"], record["plugin"]) == (
        "tenant-a",
        "user-1",
        "agent_range",
    )


def test_every_turn_of_the_job_is_pushed_in_one_round_trip(
    redis: FakeRedis, identity: None
) -> None:
    with outbox.collect_job_usage(redis):
        for _ in range(3):
            emit_usage_report(billed())
    assert len(pushed(redis)) == 3


def test_a_job_that_billed_nothing_pushes_nothing(redis: FakeRedis) -> None:
    with outbox.collect_job_usage(redis):
        pass
    assert redis.lists == {}


def test_usage_outside_a_job_is_not_collected(redis: FakeRedis, identity: None) -> None:
    emit_usage_report(billed())
    with outbox.collect_job_usage(redis):
        pass
    assert redis.lists == {}


def test_a_job_that_raises_still_hands_off_what_it_spent(
    redis: FakeRedis, identity: None
) -> None:
    with pytest.raises(RuntimeError):
        with outbox.collect_job_usage(redis):
            emit_usage_report(billed())
            raise RuntimeError("job failed")
    assert len(pushed(redis)) == 1


def test_the_outbox_is_bounded_in_size_and_age(
    redis: FakeRedis, identity: None
) -> None:
    with outbox.collect_job_usage(redis):
        emit_usage_report(billed())
    assert redis.ttl[outbox.OUTBOX_KEY] == outbox.OUTBOX_TTL_SECONDS
    redis.lists[outbox.OUTBOX_KEY] = [
        f'{{"n": {i}}}' for i in range(outbox.OUTBOX_MAX_RECORDS + 5)
    ]
    with outbox.collect_job_usage(redis):
        emit_usage_report(billed())
    assert len(redis.lists[outbox.OUTBOX_KEY]) == outbox.OUTBOX_MAX_RECORDS


def test_a_redis_outage_never_fails_the_job(redis: FakeRedis, identity: None) -> None:
    redis.broken = True
    with outbox.collect_job_usage(redis):
        emit_usage_report(billed())


def test_the_switch_turns_collection_off(
    redis: FakeRedis, identity: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(outbox.ENV_SWITCH, "false")
    with outbox.collect_job_usage(redis):
        emit_usage_report(billed())
    assert redis.lists == {}


def test_draining_returns_the_records_in_order_and_removes_them(
    redis: FakeRedis, identity: None
) -> None:
    with outbox.collect_job_usage(redis):
        emit_usage_report(billed("a/one"))
        emit_usage_report(billed("a/two"))
        emit_usage_report(billed("a/three"))
    first = outbox.drain_records(redis, limit=2)
    assert [r.model for r in first] == ["a/one", "a/two"]
    rest = outbox.drain_records(redis, limit=10)
    assert [r.model for r in rest] == ["a/three"]
    assert outbox.drain_records(redis) == []


def test_a_malformed_entry_is_skipped_not_fatal(redis: FakeRedis) -> None:
    redis.lists[outbox.OUTBOX_KEY] = [
        "not json",
        '{"model": 5}',
        json.dumps({"model": "m/x", "input_tokens": 1}),
    ]
    assert [r.model for r in outbox.drain_records(redis)] == ["m/x"]


class Seen:
    """A sink that records the identity it ran under."""

    def __init__(self) -> None:
        self.rows: list[tuple[UsageReport, str | None, str | None, str | None]] = []

    def __call__(self, report: UsageReport) -> None:
        self.rows.append(
            (
                report,
                get_current_plugin(),
                get_current_tenant_id(),
                get_current_user_id(),
            )
        )


@pytest.fixture
def seen() -> Iterator[Seen]:
    sink = Seen()
    register_usage_sink(sink)
    try:
        yield sink
    finally:
        unregister_usage_sink(sink)


def test_delivery_replays_each_turn_to_the_sinks_under_the_jobs_identity(
    redis: FakeRedis, seen: Seen
) -> None:
    tokens = (
        set_tenant_context("tenant-a"),
        set_user_context("user-1"),
        set_plugin_context("agent_range"),
    )
    try:
        with outbox.collect_job_usage(redis):
            emit_usage_report(billed(cache_read_tokens=4))
    finally:
        reset_plugin_context(tokens[2])
        reset_user_context(tokens[1])
        reset_tenant_context(tokens[0])
    seen.rows.clear()

    assert outbox.deliver_worker_usage(redis) == 1

    [(report, plugin, tenant, user)] = seen.rows
    assert (plugin, tenant, user) == ("agent_range", "tenant-a", "user-1")
    assert report.model == "ollama/qwen"
    assert (report.usage.input_tokens, report.usage.cache_read_tokens) == (100, 4)
    assert report.tenant_id == "tenant-a"


def test_delivery_leaves_no_identity_bound_afterwards(
    redis: FakeRedis, seen: Seen
) -> None:
    redis.lists[outbox.OUTBOX_KEY] = [
        json.dumps(
            {
                "model": "m/x",
                "input_tokens": 1,
                "tenant_id": "t",
                "user_id": "u",
                "plugin": "p",
            }
        )
    ]
    outbox.deliver_worker_usage(redis)
    assert get_current_plugin() in (None, "")
    assert get_current_user_id() in (None, "")


def test_a_turn_without_an_identity_is_delivered_unbound(
    redis: FakeRedis, seen: Seen
) -> None:
    redis.lists[outbox.OUTBOX_KEY] = [json.dumps({"model": "m/x", "input_tokens": 1})]
    outbox.deliver_worker_usage(redis)
    [(_, plugin, _, user)] = seen.rows
    assert plugin in (None, "") and user in (None, "")


def test_delivery_survives_a_failing_sink_and_still_counts_the_turn(
    redis: FakeRedis,
) -> None:
    def broken(report: UsageReport) -> None:
        raise RuntimeError("sink down")

    register_usage_sink(broken)
    try:
        redis.lists[outbox.OUTBOX_KEY] = [
            json.dumps({"model": "m/x", "input_tokens": 1})
        ]
        assert outbox.deliver_worker_usage(redis) == 1
    finally:
        unregister_usage_sink(broken)


def test_delivery_with_redis_down_returns_zero(redis: FakeRedis) -> None:
    redis.broken = True
    assert outbox.deliver_worker_usage(redis) == 0


def test_delivered_turns_are_not_collected_again(redis: FakeRedis, seen: Seen) -> None:
    """Delivery runs sinks, and the job collector is a sink: it must stay inert here."""
    redis.lists[outbox.OUTBOX_KEY] = [json.dumps({"model": "m/x", "input_tokens": 1})]
    outbox.deliver_worker_usage(redis)
    with outbox.collect_job_usage(redis):
        pass
    assert redis.lists.get(outbox.OUTBOX_KEY, []) == []
