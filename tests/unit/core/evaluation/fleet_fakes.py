"""An in-memory async stand-in for the Redis surface the fleet monitor uses.

Lists, hashes, sets, key expiry against a fake clock, pipelines, and the three
Lua scripts the fleet monitor and its leader lease run (compare-and-set of a
series state, compare-and-renew and compare-and-delete of the lock), so two
"workers" can share one instance the way two processes share one Redis.
"""

from __future__ import annotations

from typing import Any

from baselith.core.evaluation import online_fleet
from baselith.core.evaluation.online_monitor import QualityMonitor
from baselith.core.resilience.distributed_lock import DistributedLock


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.expiry: dict[str, float] = {}
        self.now = 0.0
        self.fail = False

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def _live(self, key: str) -> Any:
        exp = self.expiry.get(key)
        if exp is not None and self.now >= exp:
            self.data.pop(key, None)
            self.expiry.pop(key, None)
        if self.fail:
            raise ConnectionError("redis down")
        return self.data.get(key)

    # -- strings / lock ---------------------------------------------------
    async def set(self, key: str, value: str, nx: bool = False, px: int | None = None):
        if nx and self._live(key) is not None:
            return None
        self.data[key] = value
        if px is not None:
            self.expiry[key] = self.now + px / 1000.0
        return True

    # -- lists --------------------------------------------------------------
    async def lpush(self, key: str, value: str) -> int:
        items = self._live(key) or []
        items.insert(0, value)
        self.data[key] = items
        return len(items)

    async def ltrim(self, key: str, start: int, stop: int) -> bool:
        items = self._live(key) or []
        self.data[key] = items[start : stop + 1]
        return True

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self._live(key) or []
        return list(items[start:] if stop == -1 else items[start : stop + 1])

    # -- hashes -------------------------------------------------------------
    async def hincrby(self, key: str, field: str, amount: int) -> int:
        h = self._live(key) or {}
        h[field] = str(int(h.get(field, "0")) + amount)
        self.data[key] = h
        return int(h[field])

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._live(key) or {})

    async def hget(self, key: str, field: str) -> str | None:
        return (self._live(key) or {}).get(field)

    # -- sets ---------------------------------------------------------------
    async def sadd(self, key: str, *members: str) -> int:
        s = self._live(key) or set()
        s.update(members)
        self.data[key] = s
        return len(members)

    async def smembers(self, key: str) -> set[str]:
        return set(self._live(key) or set())

    async def srem(self, key: str, *members: str) -> int:
        s = self._live(key) or set()
        s.difference_update(members)
        return len(members)

    async def expire(self, key: str, seconds: int) -> bool:
        if self._live(key) is None:
            return False
        self.expiry[key] = self.now + int(seconds)
        return True

    # -- scripts ------------------------------------------------------------
    async def eval(self, script: str, numkeys: int, key: str, *args: Any) -> int:
        if script == online_fleet._TRANSITION_LUA:
            h = self._live(key) or {}
            if h.get("degraded", "0") != args[0]:
                return 0
            h.update(
                degraded=args[1], frozen_reference=args[2], transition_observed=args[3]
            )
            self.data[key] = h
            self.expiry[key] = self.now + int(args[4])
            return 1
        if self._live(key) != args[0]:
            return 0
        if "pexpire" in script:
            self.expiry[key] = self.now + int(args[1]) / 1000.0
            return 1
        self.data.pop(key, None)
        self.expiry.pop(key, None)
        return 1

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def __getattr__(self, name: str) -> Any:
        def queue(*args: Any) -> FakePipeline:
            self._calls.append((name, args))
            return self

        return queue

    async def execute(self) -> list[Any]:
        return [await getattr(self._redis, n)(*a) for n, a in self._calls]


def policy(**kw: Any) -> QualityMonitor:
    params: dict[str, Any] = {
        "window": 10,
        "reference_window": 0,
        "min_samples": 10,
        "floor": 0.5,
        "recovery_margin": 0.05,
    }
    params.update(kw)
    return QualityMonitor(**params)


def worker(
    redis: FakeRedis,
    announced: list[tuple[str, Any]],
    name: str,
    *,
    monitor: QualityMonitor | None = None,
    lock_factory: Any = None,
) -> online_fleet.FleetQualityMonitor:
    """One simulated worker sharing *redis*; its announcements land in *announced*."""
    shared = monitor or policy()
    store = online_fleet.FleetWindowStore(
        redis,
        prefix="test:eval_online",
        capacity=shared.window + shared.reference_window,
    )

    async def announce(transition: Any) -> None:
        announced.append((name, transition))

    return online_fleet.FleetQualityMonitor(
        store,
        shared,
        lock_factory=lock_factory
        or (
            lambda: DistributedLock(redis, online_fleet.LEADER_LOCK_NAME, ttl_ms=45_000)
        ),
        announce=announce,
        interval_seconds=15.0,
    )
