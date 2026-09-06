"""RATE_LIMIT_FAIL_MODE: closed => Redis outage returns 503, not N-times limit."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from core.middleware.rate_limiter import RateLimiter


def _limiter(fail_mode: str, redis_ok: bool) -> RateLimiter:
    with patch("core.middleware.rate_limiter.get_security_config") as cfg:
        cfg.return_value.rate_limit_fail_mode = fail_mode
        limiter = RateLimiter()
    if redis_ok:
        limiter._redis = AsyncMock()
        limiter._rate_limit_script = AsyncMock(return_value=[1, 0])
    else:
        limiter._redis = AsyncMock()
        limiter._rate_limit_script = AsyncMock(side_effect=ConnectionError("down"))
    return limiter


async def test_fail_open_falls_back_to_memory():
    limiter = _limiter("open", redis_ok=False)
    # Should not raise: in-memory fallback absorbs the outage.
    await limiter.check("user:x", limit=5, window_seconds=60)


async def test_fail_closed_returns_503_on_redis_outage():
    limiter = _limiter("closed", redis_ok=False)
    with pytest.raises(HTTPException) as exc:
        await limiter.check("user:x", limit=5, window_seconds=60)
    assert exc.value.status_code == 503


async def test_fail_closed_normal_path_unaffected():
    limiter = _limiter("closed", redis_ok=True)
    await limiter.check("user:x", limit=5, window_seconds=60)


def test_unset_mode_resolves_closed_only_for_redis_backed_production(monkeypatch):
    from types import SimpleNamespace

    from core.middleware.rate_limiter import _resolve_fail_mode

    def _storage(backend: str):
        return lambda: SimpleNamespace(cache_backend=backend)

    monkeypatch.setattr("core.config.environment.is_production_env", lambda: True)
    monkeypatch.setattr("core.config.get_storage_config", _storage("redis"))
    assert _resolve_fail_mode(None) == "closed"
    # Production without a Redis backend: the in-memory window is the design,
    # not a degraded state — refusing every request would be a self-inflicted
    # outage rather than a security control.
    monkeypatch.setattr("core.config.get_storage_config", _storage("memory"))
    assert _resolve_fail_mode(None) == "open"
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: False)
    monkeypatch.setattr("core.config.get_storage_config", _storage("redis"))
    assert _resolve_fail_mode(None) == "open"
    # An explicit value always wins over the environment.
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: True)
    assert _resolve_fail_mode("open") == "open"
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: False)
    assert _resolve_fail_mode("closed") == "closed"
