"""Fleet-wide quality monitoring for online evaluation, shared through Redis.

With several workers (``WEB_CONCURRENCY`` or replicas) the in-process
:class:`~.online_monitor.QualityMonitor` sees only the traffic its own worker
sampled, and every worker that crossed a threshold announced it. When the
cache backend is Redis (``CACHE_BACKEND=redis``) and ``EVAL_ONLINE_FLEET`` is
on, the windows are shared instead:

* **Every worker records.** Each score is pushed onto its series' Redis list
  (newest first, trimmed to ``EVAL_ONLINE_WINDOW + EVAL_ONLINE_REFERENCE_WINDOW``)
  and counted on the series' state hash — one pipelined round trip.
* **One worker decides.** Every ``EVAL_ONLINE_FLEET_INTERVAL_SECONDS`` each
  worker ticks; the one holding the ``eval_online_monitor``
  :class:`~baselith.core.resilience.distributed_lock.DistributedLock` lease (taken
  non-blocking, renewed every tick, expiring after three missed ticks) reads
  every series, applies :meth:`QualityMonitor.assess` to the fleet window and
  announces each transition once.
* **State changes are compare-and-set.** A transition is written with a Lua
  script that only applies when the series is still in the state the decision
  was made from, and only the writer that applied it announces. Two workers
  that briefly both believe they lead (a lease that expired under a paused
  holder) therefore cannot announce the same transition twice.
* **Every worker mirrors the gauge.** Each tick sets
  ``mas_eval_online_degraded`` from the shared state on every worker, so the
  gauge (``livemax`` across processes) is the fleet's and survives a change of
  leader.

Series keys expire after :data:`SERIES_TTL_SECONDS` without a new score; the
leader drops expired series from the index. Redis failures are counted as
``mas_eval_online_failures_total{stage="fleet"}`` and never reach the request
path; while Redis is unreachable nothing is announced (no worker falls back to
announcing its own share, which would bring the N-fold events back).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from baselith.core.observability.logging import get_logger

from .online_metrics import record_failure, set_degraded
from .online_monitor import DEGRADED, MonitorTransition, QualityMonitor

logger = get_logger(__name__)

#: Seconds a series' Redis keys live after its last score.
SERIES_TTL_SECONDS = 7 * 86_400
#: Name of the leader lease (``baselithcore:lock:<name>``).
LEADER_LOCK_NAME = "eval_online_monitor"
#: Missed ticks after which a silent leader's lease expires.
LEASE_TICKS = 3

# Apply a state change only if the series is still in the expected state.
# KEYS[1] = state hash; ARGV = expected, new, frozen reference, observed, ttl.
_TRANSITION_LUA = """
local current = redis.call('hget', KEYS[1], 'degraded') or '0'
if current ~= ARGV[1] then
    return 0
end
redis.call('hset', KEYS[1], 'degraded', ARGV[2], 'frozen_reference', ARGV[3],
           'transition_observed', ARGV[4])
redis.call('expire', KEYS[1], ARGV[5])
return 1
"""


class LeaderLock(Protocol):
    """The lease surface of :class:`~baselith.core.resilience.distributed_lock.DistributedLock`."""

    async def acquire(self, *, blocking: bool = True) -> bool: ...

    async def renew(self) -> bool: ...

    async def release(self) -> bool: ...


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


@dataclass(frozen=True, slots=True)
class FleetSeries:
    """One series as the fleet holds it."""

    intent: str
    source: str
    scores: list[float]
    observed: int
    degraded: bool
    frozen_reference: float | None
    transition_observed: int

    @property
    def key(self) -> tuple[str, str]:
        """``(intent, source)``."""
        return self.intent, self.source

    @property
    def since_transition(self) -> int:
        """Scores observed fleet-wide since the last state change."""
        return self.observed - self.transition_observed


class FleetWindowStore:
    """Score windows and monitor state of every series, in Redis.

    Args:
        redis: A ``redis.asyncio.Redis``-compatible client.
        prefix: Key prefix (``<CACHE_REDIS_PREFIX>:eval_online``).
        capacity: Scores kept per series (window + reference window).
        ttl_seconds: Idle lifetime of a series' keys.
    """

    def __init__(
        self,
        redis: Any,
        *,
        prefix: str,
        capacity: int,
        ttl_seconds: int = SERIES_TTL_SECONDS,
    ) -> None:
        self._redis = redis
        self._index = f"{prefix}:series"
        self._prefix = prefix
        self.capacity = max(1, capacity)
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _member(intent: str, source: str) -> str:
        return json.dumps([intent, source], separators=(",", ":"))

    def _scores_key(self, member: str) -> str:
        return f"{self._prefix}:scores:{member}"

    def _state_key(self, member: str) -> str:
        return f"{self._prefix}:state:{member}"

    async def push(self, intent: str, source: str, score: float) -> None:
        """Record one score of a series."""
        member = self._member(intent, source)
        scores, state = self._scores_key(member), self._state_key(member)
        pipe = self._redis.pipeline(transaction=False)
        pipe.lpush(scores, repr(float(score)))
        pipe.ltrim(scores, 0, self.capacity - 1)
        pipe.hincrby(state, "observed", 1)
        pipe.sadd(self._index, member)
        pipe.expire(scores, self.ttl_seconds)
        pipe.expire(state, self.ttl_seconds)
        pipe.expire(self._index, self.ttl_seconds)
        await pipe.execute()

    async def _members(self) -> list[str]:
        return sorted(_text(m) for m in await self._redis.smembers(self._index))

    async def read_all(self) -> list[FleetSeries]:
        """Every live series, sorted; expired ones are dropped from the index."""
        members = await self._members()
        if not members:
            return []
        pipe = self._redis.pipeline(transaction=False)
        for member in members:
            pipe.lrange(self._scores_key(member), 0, -1)
            pipe.hgetall(self._state_key(member))
        results = await pipe.execute()
        series: list[FleetSeries] = []
        stale: list[str] = []
        for i, member in enumerate(members):
            raw_scores, raw_state = results[2 * i], results[2 * i + 1] or {}
            if not raw_scores:
                stale.append(member)
                continue
            state = {_text(k): _text(v) for k, v in raw_state.items()}
            intent, source = json.loads(member)
            frozen = state.get("frozen_reference") or ""
            series.append(
                FleetSeries(
                    intent=intent,
                    source=source,
                    scores=[float(_text(s)) for s in reversed(raw_scores)],
                    observed=int(state.get("observed", "0")),
                    degraded=state.get("degraded") == "1",
                    frozen_reference=float(frozen) if frozen else None,
                    transition_observed=int(state.get("transition_observed", "0")),
                )
            )
        if stale:
            await self._redis.srem(self._index, *stale)
        return series

    async def degraded_states(self) -> dict[tuple[str, str], bool]:
        """``{(intent, source): degraded}`` for every indexed series."""
        members = await self._members()
        if not members:
            return {}
        pipe = self._redis.pipeline(transaction=False)
        for member in members:
            pipe.hget(self._state_key(member), "degraded")
        values = await pipe.execute()
        states: dict[tuple[str, str], bool] = {}
        for member, value in zip(members, values, strict=True):
            intent, source = json.loads(member)
            states[(intent, source)] = value is not None and _text(value) == "1"
        return states

    async def apply(self, series: FleetSeries, transition: MonitorTransition) -> bool:
        """Write *transition* if *series* is still in the state it was read in.

        Returns:
            Whether this caller applied it (and so must announce it).
        """
        degraded = transition.state == DEGRADED
        frozen = transition.reference_mean if degraded else None
        result = await self._redis.eval(
            _TRANSITION_LUA,
            1,
            self._state_key(self._member(series.intent, series.source)),
            "1" if series.degraded else "0",
            "1" if degraded else "0",
            repr(frozen) if frozen is not None else "",
            str(series.observed),
            str(self.ttl_seconds),
        )
        return bool(result)


Announce = Callable[[MonitorTransition], Awaitable[None]]


class FleetQualityMonitor:
    """Record scores fleet-wide; one elected worker announces transitions.

    Args:
        store: The shared windows.
        policy: The thresholds (its :meth:`QualityMonitor.assess` decides).
        lock_factory: Builds a fresh leader lease.
        announce: Called once per applied transition.
        interval_seconds: Seconds between two ticks.
    """

    def __init__(
        self,
        store: FleetWindowStore,
        policy: QualityMonitor,
        *,
        lock_factory: Callable[[], LeaderLock],
        announce: Announce,
        interval_seconds: float = 15.0,
    ) -> None:
        self.store = store
        self.policy = policy
        self.interval_seconds = interval_seconds
        self._lock_factory = lock_factory
        self._announce = announce
        self._lock: LeaderLock | None = None
        self._leader = False
        self._gauged: set[tuple[str, str]] = set()

    @property
    def leader(self) -> bool:
        """Whether this worker held the lease at its last tick."""
        return self._leader

    async def record(self, intent: str, source: str, score: float) -> bool:
        """Push one score; ``False`` (and counted) when Redis failed."""
        try:
            await self.store.push(intent, source, score)
            return True
        except Exception as exc:
            record_failure("fleet")
            logger.warning("online_eval_fleet_record_failed: %s", exc)
            return False

    async def _elect(self) -> None:
        if self._lock is None:
            self._lock = self._lock_factory()
        try:
            if self._leader:
                self._leader = bool(await self._lock.renew())
                if not self._leader:
                    logger.warning("online_eval_fleet_leadership_lost")
                    self._lock = None
            else:
                self._leader = bool(await self._lock.acquire(blocking=False))
                if self._leader:
                    logger.info("online_eval_fleet_leader_elected")
        except Exception:
            self._leader, self._lock = False, None
            raise

    async def tick(self) -> list[MonitorTransition]:
        """Elect, decide (leader only) and mirror the gauge; the applied transitions."""
        await self._elect()
        if not self._leader:
            self._mirror(await self.store.degraded_states())
            return []
        applied: list[MonitorTransition] = []
        states: dict[tuple[str, str], bool] = {}
        for series in await self.store.read_all():
            states[series.key] = series.degraded
            transition = self.policy.assess(
                series.intent,
                series.source,
                series.scores,
                since_transition=series.since_transition,
                degraded=series.degraded,
                frozen_reference=series.frozen_reference,
            )
            if transition is None or not await self.store.apply(series, transition):
                continue
            states[series.key] = transition.state == DEGRADED
            applied.append(transition)
            try:
                await self._announce(transition)
            except Exception as exc:
                record_failure("monitor")
                logger.warning("online_eval_fleet_announce_failed: %s", exc)
        self._mirror(states)
        return applied

    def _mirror(self, states: dict[tuple[str, str], bool]) -> None:
        """Set the gauge to the fleet state; series gone from it read 0."""
        for key in self._gauged - states.keys():
            set_degraded(*key, False)
        for key, degraded in states.items():
            set_degraded(*key, degraded)
        self._gauged = set(states)

    async def run(self) -> None:
        """Tick until cancelled; a failed tick is counted and retried."""
        try:
            while True:
                try:
                    await self.tick()
                except Exception as exc:
                    record_failure("fleet")
                    logger.warning("online_eval_fleet_tick_failed: %s", exc)
                await asyncio.sleep(self.interval_seconds)
        finally:
            await self._resign()

    async def _resign(self) -> None:
        lock, self._lock, self._leader = self._lock, None, False
        if lock is None:
            return
        try:
            # Shielded: a cancelled run loop still hands the lease back.
            await asyncio.shield(lock.release())
        except (Exception, asyncio.CancelledError) as exc:
            logger.debug("online_eval_fleet_release_failed: %s", exc)

    async def summary(self) -> list[dict[str, Any]]:
        """Fleet window statistics per series, sorted by intent then source."""
        return [
            self.policy.series_row(
                s.intent,
                s.source,
                s.scores,
                observed=s.observed,
                degraded=s.degraded,
                frozen_reference=s.frozen_reference,
            )
            for s in await self.store.read_all()
        ]


def build_fleet_monitor(
    *,
    enabled: bool,
    policy: QualityMonitor,
    announce: Announce,
    interval_seconds: float,
) -> FleetQualityMonitor | None:
    """The fleet monitor when Redis is the cache backend, else ``None``.

    Args:
        enabled: ``EVAL_ONLINE_FLEET``.
        policy: The thresholds.
        announce: Called once per applied transition.
        interval_seconds: ``EVAL_ONLINE_FLEET_INTERVAL_SECONDS``.

    Returns:
        The monitor, or ``None`` for per-process monitoring (fleet off, the
        cache backend is not Redis, or no client could be built).
    """
    if not enabled:
        return None
    from baselith.core.config import get_storage_config

    storage = get_storage_config()
    if storage.cache_backend != "redis" or not storage.cache_redis_url:
        return None
    try:
        from baselith.core.cache.redis_cache import create_redis_client
        from baselith.core.resilience.distributed_lock import DistributedLock

        client = create_redis_client(storage.cache_redis_url, decode_responses=True)
    except Exception as exc:
        logger.warning("online_eval_fleet_unavailable (per-process monitor): %s", exc)
        return None
    ttl_ms = max(1000, int(interval_seconds * LEASE_TICKS * 1000))
    store = FleetWindowStore(
        client,
        prefix=f"{storage.cache_redis_prefix}:eval_online",
        capacity=policy.window + policy.reference_window,
    )
    return FleetQualityMonitor(
        store,
        policy,
        lock_factory=lambda: DistributedLock(client, LEADER_LOCK_NAME, ttl_ms=ttl_ms),
        announce=announce,
        interval_seconds=interval_seconds,
    )


__all__ = [
    "LEADER_LOCK_NAME",
    "SERIES_TTL_SECONDS",
    "FleetQualityMonitor",
    "FleetSeries",
    "FleetWindowStore",
    "build_fleet_monitor",
]
