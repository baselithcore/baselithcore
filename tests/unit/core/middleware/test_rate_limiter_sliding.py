"""Sliding-window semantics of ``RateLimiter`` (Redis path and in-memory fallback).

A fixed window admits up to 2x the limit across a window boundary (N requests
at t=59s, N more at t=61s). The weighted sliding window counts the previous
window proportionally to how much of it still overlaps the trailing interval,
so that burst is rejected.
"""

from __future__ import annotations

import asyncio
import importlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from core.middleware.rate_limiter import (
    RateLimiter,
    _sliding_estimate,
    _sliding_retry_after,
)

_WINDOW = 60
# ``core.middleware.rate_limiter`` the *module*: the package re-exports a
# ``rate_limiter`` proxy object under the same name, so attribute access
# (``import a.b as x``) would resolve to the proxy instead.
rate_limiter_module = importlib.import_module("core.middleware.rate_limiter")


def test_estimate_weights_previous_window_by_remaining_overlap():
    # 10s into the window: 50/60 of the previous window still overlaps.
    assert _sliding_estimate(
        prev=12, current=3, window_seconds=_WINDOW, elapsed=10.0
    ) == pytest.approx(13.0)


def test_estimate_at_window_start_counts_full_previous_window():
    assert _sliding_estimate(
        prev=10, current=1, window_seconds=_WINDOW, elapsed=0.0
    ) == pytest.approx(11.0)


def test_estimate_at_window_end_ignores_previous_window():
    assert _sliding_estimate(
        prev=10, current=1, window_seconds=_WINDOW, elapsed=60.0
    ) == pytest.approx(1.0)


def test_retry_after_when_previous_window_dominates():
    # limit 10, prev 12, current 1 at t=0: admitted once 12*w + 1 + 1 <= 10,
    # i.e. w <= 8/12 -> elapsed >= 20s.
    assert (
        _sliding_retry_after(
            prev=12, current=1, limit=10, window_seconds=_WINDOW, elapsed=0.0
        )
        == 20
    )


def test_retry_after_when_current_window_is_full_waits_for_rollover():
    # limit 10, current 10 at t=30, prev 0: next window needs 10*w' + 1 <= 10,
    # i.e. w' <= 0.9 -> 6s into the next window -> 30s to rollover + 6s.
    assert (
        _sliding_retry_after(
            prev=0, current=10, limit=10, window_seconds=_WINDOW, elapsed=30.0
        )
        == 36
    )


def test_retry_after_is_never_negative():
    assert (
        _sliding_retry_after(
            prev=1, current=1, limit=10, window_seconds=_WINDOW, elapsed=59.0
        )
        == 0
    )


def _fallback_limiter() -> RateLimiter:
    rl = RateLimiter.__new__(RateLimiter)
    rl._fallback = {}
    rl._fallback_lock = asyncio.Lock()
    rl._fallback_checks_since_prune = 0
    return rl


async def test_fallback_rejects_burst_across_window_boundary(monkeypatch):
    rl = _fallback_limiter()
    clock = {"t": 100 * _WINDOW + 59.0}  # 59s into window #100
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: clock["t"])
    for _ in range(10):
        await rl._check_fallback("k", limit=10, window_seconds=_WINDOW)

    clock["t"] += 2.0  # 1s into window #101: previous window still weighs 59/60
    with pytest.raises(HTTPException) as exc_info:
        await rl._check_fallback("k", limit=10, window_seconds=_WINDOW)
    assert exc_info.value.status_code == 429
    assert exc_info.value.headers["RateLimit-Limit"] == "10"


async def test_fallback_admits_once_previous_window_has_decayed(monkeypatch):
    rl = _fallback_limiter()
    clock = {"t": 100 * _WINDOW + 59.0}
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: clock["t"])
    for _ in range(10):
        await rl._check_fallback("k", limit=10, window_seconds=_WINDOW)

    clock["t"] += 31.0  # 30s into window #101: 10 * 0.5 + 1 = 6 <= 10
    await rl._check_fallback("k", limit=10, window_seconds=_WINDOW)


async def test_fallback_forgets_windows_older_than_previous(monkeypatch):
    rl = _fallback_limiter()
    clock = {"t": 100 * _WINDOW + 59.0}
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: clock["t"])
    for _ in range(10):
        await rl._check_fallback("k", limit=10, window_seconds=_WINDOW)

    clock["t"] += 2 * _WINDOW  # window #102: #100 is no longer "previous"
    await rl._check_fallback("k", limit=10, window_seconds=_WINDOW)
    count, _index, prev = rl._fallback["k"]
    assert (count, prev) == (1, 0)


def _redis_limiter(script_result: list[int]) -> RateLimiter:
    with patch("core.middleware.rate_limiter.get_security_config") as cfg:
        cfg.return_value.rate_limit_fail_mode = "open"
        limiter = RateLimiter()
    limiter._redis = AsyncMock()
    limiter._rate_limit_script = AsyncMock(return_value=script_result)
    return limiter


async def test_redis_path_rejects_on_weighted_estimate(monkeypatch):
    limiter = _redis_limiter([1, 12])  # current=1, previous=12
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: 100 * _WINDOW + 0.0)
    with pytest.raises(HTTPException) as exc_info:
        await limiter.check("user:x", limit=10, window_seconds=_WINDOW)
    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.headers["Retry-After"] == "20"
    assert exc.headers["RateLimit-Remaining"] == "0"


async def test_redis_path_admits_under_weighted_estimate(monkeypatch):
    limiter = _redis_limiter([5, 8])  # 30s in: 8 * 0.5 + 5 = 9 <= 10
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: 100 * _WINDOW + 30.0)
    await limiter.check("user:x", limit=10, window_seconds=_WINDOW)


async def test_redis_keys_are_per_window_and_share_a_hash_tag(monkeypatch):
    limiter = _redis_limiter([1, 0])
    monkeypatch.setattr(rate_limiter_module.time, "time", lambda: 100 * _WINDOW + 5.0)
    await limiter.check("user:x", limit=10, window_seconds=_WINDOW)
    call = limiter._rate_limit_script.await_args
    keys = call.kwargs["keys"]
    assert len(keys) == 2
    assert keys[0].endswith(":100") and keys[1].endswith(":99")
    # Same Redis Cluster slot: both keys carry the identical {hash-tag}.
    tags = {k[k.index("{") + 1 : k.index("}")] for k in keys}
    assert len(tags) == 1
    assert "user:x" in tags.pop()
    # Both window keys must outlive the window they are consulted in.
    assert call.kwargs["args"] == [2 * _WINDOW]
