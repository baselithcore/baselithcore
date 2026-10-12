"""Fleet-wide online quality monitoring: shared windows, one announcer, fallback."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from baselith.core.evaluation import online_fleet
from baselith.core.evaluation.online_monitor import DEGRADED, RECOVERED
from baselith.core.observability.metrics import EVAL_ONLINE_DEGRADED
from tests.unit.core.evaluation.fleet_fakes import FakeRedis, policy, worker


def _gauge(intent: str, source: str = "checks") -> float:
    return EVAL_ONLINE_DEGRADED.labels(intent=intent, source=source)._value.get()


async def test_two_workers_share_one_window() -> None:
    redis, announced = FakeRedis(), []
    a, b = worker(redis, announced, "a"), worker(redis, announced, "b")
    for _ in range(6):
        await a.record("fleet-qa", "checks", 1.0)
    for _ in range(4):
        await b.record("fleet-qa", "checks", 0.5)

    for w in (a, b):
        (row,) = await w.summary()
        assert row["window_samples"] == 10
        assert row["observed"] == 10
        assert row["window_mean"] == pytest.approx(0.8)


async def test_degradation_visible_only_fleet_wide_is_announced_once() -> None:
    """Five bad scores per worker: neither worker alone reaches min_samples."""
    redis, announced = FakeRedis(), []
    a, b = worker(redis, announced, "a"), worker(redis, announced, "b")
    for w in (a, b):
        alone = policy()  # what this worker's per-process monitor would see
        for _ in range(5):
            await w.record("fleet-deg", "checks", 0.1)
            assert alone.observe("fleet-deg", "checks", 0.1) is None

    await a.tick()  # a takes the lease and decides
    await b.tick()  # b follows: mirrors the gauge, announces nothing
    await a.tick()  # nothing new: no second announcement

    assert [(who, t.state) for who, t in announced] == [("a", DEGRADED)]
    assert (a.leader, b.leader) == (True, False)
    assert announced[0][1].samples == 10
    assert _gauge("fleet-deg") == 1


async def test_two_leaders_cannot_announce_the_same_transition_twice() -> None:
    """A lease that expired under a paused holder can leave two workers both
    believing they lead; the compare-and-set lets only one announce."""
    redis, announced = FakeRedis(), []
    always = MagicMock()
    always.acquire = _async(True)
    always.renew = _async(True)
    always.release = _async(True)
    a = worker(redis, announced, "a", lock_factory=lambda: always)
    b = worker(redis, announced, "b", lock_factory=lambda: always)
    for _ in range(10):
        await a.record("fleet-race", "checks", 0.0)

    snapshot = await a.store.read_all()
    # Both decide from the same read; b applies first.
    transition = a.policy.assess(
        "fleet-race",
        "checks",
        snapshot[0].scores,
        since_transition=snapshot[0].since_transition,
        degraded=False,
        frozen_reference=None,
    )
    assert transition is not None
    assert await b.store.apply(snapshot[0], transition) is True
    assert await a.store.apply(snapshot[0], transition) is False

    await a.tick()
    await b.tick()
    assert announced == []  # already degraded: nothing left to announce


async def test_failover_keeps_the_state_and_the_new_leader_recovers() -> None:
    redis, announced = FakeRedis(), []
    a, b = worker(redis, announced, "a"), worker(redis, announced, "b")
    for _ in range(10):
        await a.record("fleet-fail", "checks", 0.1)
    await a.tick()
    assert [t.state for _, t in announced] == [DEGRADED]

    # a stops ticking (crash, pause): its lease lapses after three ticks.
    await b.tick()
    assert b.leader is False
    redis.advance(46)
    for _ in range(10):
        await b.record("fleet-fail", "checks", 1.0)
    await b.tick()

    assert b.leader is True
    assert [(who, t.state) for who, t in announced] == [
        ("a", DEGRADED),
        ("b", RECOVERED),
    ]
    assert _gauge("fleet-fail") == 0


async def test_recovery_needs_min_samples_since_the_transition() -> None:
    redis, announced = FakeRedis(), []
    a = worker(redis, announced, "a", monitor=policy(window=4, min_samples=4))
    for _ in range(4):
        await a.record("fleet-hyst", "checks", 0.0)
    await a.tick()
    for _ in range(3):
        await a.record("fleet-hyst", "checks", 1.0)
    await a.tick()
    assert [t.state for _, t in announced] == [DEGRADED]  # only 3 since
    await a.record("fleet-hyst", "checks", 1.0)
    await a.tick()
    assert [t.state for _, t in announced] == [DEGRADED, RECOVERED]


async def test_expired_series_leave_the_index_and_the_gauge() -> None:
    redis, announced = FakeRedis(), []
    a, b = worker(redis, announced, "a"), worker(redis, announced, "b")
    for _ in range(10):
        await a.record("fleet-idle", "checks", 0.0)
    await a.tick()
    await b.tick()
    assert _gauge("fleet-idle") == 1

    redis.advance(online_fleet.SERIES_TTL_SECONDS + 1)
    await a.tick()  # a's lease lapsed too: it steps down, the index is gone
    assert await a.summary() == []
    assert await redis.smembers("test:eval_online:series") == set()
    assert _gauge("fleet-idle") == 0


async def test_redis_failure_is_counted_never_raised() -> None:
    redis, announced = FakeRedis(), []
    a = worker(redis, announced, "a")
    redis.fail = True
    assert await a.record("fleet-down", "checks", 0.5) is False
    with pytest.raises(ConnectionError):
        await a.tick()
    assert a.leader is False


async def test_run_resigns_the_lease_when_cancelled() -> None:
    import asyncio

    redis, announced = FakeRedis(), []
    a = worker(redis, announced, "a")
    task = asyncio.create_task(a.run())
    for _ in range(20):
        await asyncio.sleep(0)
    assert a.leader is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert redis.data.get(f"baselithcore:lock:{online_fleet.LEADER_LOCK_NAME}") is None


# -- construction: when the fleet is on --------------------------------------


def _storage(backend: str) -> Any:
    storage = MagicMock()
    storage.cache_backend = backend
    storage.cache_redis_url = "redis://cache:6379/1"
    storage.cache_redis_prefix = "bc"
    return storage


async def _announce(_: Any) -> None:
    return None


def test_build_is_off_without_redis_or_when_disabled(monkeypatch) -> None:
    import baselith.core.config as config

    monkeypatch.setattr(config, "get_storage_config", lambda: _storage("local"))
    assert (
        online_fleet.build_fleet_monitor(
            enabled=True, policy=policy(), announce=_announce, interval_seconds=1
        )
        is None
    )
    monkeypatch.setattr(config, "get_storage_config", lambda: _storage("redis"))
    assert (
        online_fleet.build_fleet_monitor(
            enabled=False, policy=policy(), announce=_announce, interval_seconds=1
        )
        is None
    )


def test_build_uses_the_cache_redis_when_it_is_the_backend(monkeypatch) -> None:
    import baselith.core.cache.redis_cache as redis_cache
    import baselith.core.config as config

    monkeypatch.setattr(config, "get_storage_config", lambda: _storage("redis"))
    seen: dict[str, Any] = {}

    def client(url: str, *, decode_responses: bool = False) -> FakeRedis:
        seen.update(url=url, decode=decode_responses)
        return FakeRedis()

    monkeypatch.setattr(redis_cache, "create_redis_client", client)
    fleet = online_fleet.build_fleet_monitor(
        enabled=True, policy=policy(), announce=_announce, interval_seconds=10
    )
    assert fleet is not None
    assert seen == {"url": "redis://cache:6379/1", "decode": True}
    assert fleet.store.capacity == 10
    assert fleet._lock_factory()._ttl_ms == 30_000  # three ticks


def test_build_falls_back_when_no_client_can_be_built(monkeypatch) -> None:
    import baselith.core.cache.redis_cache as redis_cache
    import baselith.core.config as config

    monkeypatch.setattr(config, "get_storage_config", lambda: _storage("redis"))

    def broken(url: str, *, decode_responses: bool = False) -> Any:
        raise RuntimeError("redis package is not installed.")

    monkeypatch.setattr(redis_cache, "create_redis_client", broken)
    assert (
        online_fleet.build_fleet_monitor(
            enabled=True, policy=policy(), announce=_announce, interval_seconds=1
        )
        is None
    )


def _async(value: Any) -> Any:
    async def call(*_: Any, **__: Any) -> Any:
        return value

    return call
