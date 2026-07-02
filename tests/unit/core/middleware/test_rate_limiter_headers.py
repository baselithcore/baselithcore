"""Tests for RateLimiter 429 standard headers (Retry-After / RateLimit-*)."""

import asyncio

import pytest
from fastapi import HTTPException

from core.middleware.rate_limiter import RateLimiter, _rate_limit_headers


def test_headers_shape():
    h = _rate_limit_headers(limit=10, current=11, reset_seconds=42)
    assert h["Retry-After"] == "42"
    assert h["RateLimit-Limit"] == "10"
    assert h["RateLimit-Remaining"] == "0"
    assert h["RateLimit-Reset"] == "42"


def test_remaining_is_clamped_and_reset_non_negative():
    h = _rate_limit_headers(limit=5, current=2, reset_seconds=-3)
    assert h["RateLimit-Remaining"] == "3"
    assert h["Retry-After"] == "0"
    assert h["RateLimit-Reset"] == "0"


@pytest.mark.asyncio
async def test_fallback_429_carries_standard_headers():
    # Build a limiter without touching Redis; exercise the in-memory fallback.
    rl = RateLimiter.__new__(RateLimiter)
    rl._fallback = {}
    rl._fallback_lock = asyncio.Lock()

    # limit=1: the first call is allowed, the second breaches.
    await rl._check_fallback("tenant-a:user:ip", limit=1, window_seconds=30)
    with pytest.raises(HTTPException) as exc_info:
        await rl._check_fallback("tenant-a:user:ip", limit=1, window_seconds=30)

    exc = exc_info.value
    assert exc.status_code == 429
    assert exc.headers is not None
    assert exc.headers["RateLimit-Limit"] == "1"
    assert exc.headers["RateLimit-Remaining"] == "0"
    assert int(exc.headers["Retry-After"]) >= 0
