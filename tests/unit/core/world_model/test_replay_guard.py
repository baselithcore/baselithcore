"""The AP2 replay ledger must be shared across workers and fail closed.

A process-local ledger degrades replay protection to "once per worker" as soon
as WEB_CONCURRENCY > 1 — i.e. one authorized purchase executed N times. The
Redis-backed guard claims the intent id with an atomic SET NX, and refuses to
answer at all when the ledger is unreachable (unknown must read as refused for
a payment authorization).
"""

from __future__ import annotations

from typing import Any

import pytest

from core.world_model.replay_guard import (
    RedisReplayGuard,
    ReplayLedgerUnavailableError,
    build_default_replay_guard,
)


class _FakeRedis:
    """Minimal stand-in implementing SET ... NX EX semantics."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.calls: list[tuple[Any, Any, Any]] = []

    def set(self, key, value, nx=False, ex=None):
        self.calls.append((key, nx, ex))
        if nx and key in self.store:
            return None  # redis-py returns None when NX did not write
        self.store[key] = value
        return True


class _BrokenRedis:
    def set(self, *a, **k):
        raise ConnectionError("redis down")


def test_first_use_claims_and_replay_is_refused() -> None:
    guard = RedisReplayGuard(_FakeRedis())
    assert guard.register_once("intent_abc") is True
    assert guard.register_once("intent_abc") is False


def test_distinct_intents_are_independent() -> None:
    guard = RedisReplayGuard(_FakeRedis())
    assert guard.register_once("intent_a") is True
    assert guard.register_once("intent_b") is True


def test_claim_uses_nx_and_a_ttl() -> None:
    """The write must be an atomic NX claim with an expiry, never a bare SET:
    without NX two workers both read 'first use'."""
    client = _FakeRedis()
    RedisReplayGuard(client, ttl_seconds=1234).register_once("intent_x")

    key, nx, ex = client.calls[0]
    assert nx is True
    assert ex == 1234
    assert "intent_x" in key


def test_unreachable_ledger_fails_closed() -> None:
    """A guard that cannot prove the intent is unused must not report it as
    unused — it raises instead of returning True."""
    guard = RedisReplayGuard(_BrokenRedis())
    with pytest.raises(ReplayLedgerUnavailableError):
        guard.register_once("intent_abc")


def _patch_storage(monkeypatch, *, backend: str, url: str) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(
        "core.config.get_storage_config",
        lambda: SimpleNamespace(cache_backend=backend, cache_redis_url=url),
    )
    monkeypatch.setattr("redis.Redis.from_url", lambda url: _FakeRedis())
    monkeypatch.setattr("core.config.environment.is_production_env", lambda: False)


def test_default_guard_is_redis_backed_when_running_on_redis(monkeypatch) -> None:
    _patch_storage(monkeypatch, backend="redis", url="redis://localhost:6379/0")
    assert isinstance(build_default_replay_guard(), RedisReplayGuard)


def test_default_guard_falls_back_to_memory_without_cache_url(monkeypatch) -> None:
    from core.world_model.mandates import InMemoryReplayGuard

    _patch_storage(monkeypatch, backend="redis", url="")
    assert isinstance(build_default_replay_guard(), InMemoryReplayGuard)


def test_local_cache_backend_does_not_select_redis(monkeypatch) -> None:
    """CACHE_REDIS_URL ships with a non-empty default while CACHE_BACKEND
    defaults to 'local'. Selecting on the URL alone would hand a stock config
    a fail-closed Redis guard with no Redis behind it — turning every mandate
    verification into an error instead of falling back to the in-memory guard.
    """
    from core.world_model.mandates import InMemoryReplayGuard

    _patch_storage(monkeypatch, backend="local", url="redis://localhost:6379/1")
    assert isinstance(build_default_replay_guard(), InMemoryReplayGuard)
