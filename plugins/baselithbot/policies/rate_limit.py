"""Sliding-window rate limiter keyed by ``(channel, sender)``."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimitState:
    window_seconds: float
    max_events: int
    events: deque[float] = field(default_factory=deque)


class RateLimiter:
    """In-memory sliding-window rate limiter."""

    #: Bucket count that triggers a sweep of idle buckets. Keys are per client
    #: host (and, for some routes, per path parameter), so without eviction an
    #: attacker rotating source addresses grows the dict without bound.
    SWEEP_THRESHOLD = 4096

    def __init__(self, window_seconds: float = 60.0, max_events: int = 30) -> None:
        self._window = window_seconds
        self._max = max_events
        self._buckets: dict[str, RateLimitState] = {}
        self._next_sweep_at = self.SWEEP_THRESHOLD

    def _sweep(self, now: float) -> None:
        """Drop buckets whose every event has left the window.

        The next sweep is scheduled at twice the surviving size, so a flood
        of still-active keys costs amortised O(1) per new key, not O(n).
        """
        cutoff = now - self._window
        idle = [k for k, b in self._buckets.items() if not b.events or b.events[-1] < cutoff]
        for key in idle:
            del self._buckets[key]
        self._next_sweep_at = max(self.SWEEP_THRESHOLD, 2 * len(self._buckets))

    def _bucket(self, key: str) -> RateLimitState:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = RateLimitState(window_seconds=self._window, max_events=self._max)
            self._buckets[key] = bucket
        return bucket

    def consume(self, key: str) -> bool:
        """Record one event under ``key``; return ``False`` if over the limit."""
        now = time.time()
        if key not in self._buckets and len(self._buckets) >= self._next_sweep_at:
            self._sweep(now)
        bucket = self._bucket(key)
        cutoff = now - bucket.window_seconds
        while bucket.events and bucket.events[0] < cutoff:
            bucket.events.popleft()
        if len(bucket.events) >= bucket.max_events:
            return False
        bucket.events.append(now)
        return True

    def remaining(self, key: str) -> int:
        bucket = self._bucket(key)
        cutoff = time.time() - bucket.window_seconds
        while bucket.events and bucket.events[0] < cutoff:
            bucket.events.popleft()
        return max(0, bucket.max_events - len(bucket.events))

    def status(self) -> dict[str, dict[str, float]]:
        return {
            key: {
                "events_in_window": len(b.events),
                "max_events": b.max_events,
                "window_seconds": b.window_seconds,
            }
            for key, b in self._buckets.items()
        }


__all__ = ["RateLimiter", "RateLimitState"]
