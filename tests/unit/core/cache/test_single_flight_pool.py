"""Redis single-flight borrows the shared bounded pool instead of a private client."""

from __future__ import annotations

from core.cache import redis_cache, single_flight


def test_redis_single_flight_uses_shared_pool(monkeypatch):
    seen: dict = {}

    def _fake_create(url, *, decode_responses=False):
        seen["url"] = url
        seen["decode_responses"] = decode_responses
        return object()

    monkeypatch.setattr(redis_cache, "create_redis_client", _fake_create)
    sf = single_flight.RedisSingleFlight(url="redis://example:6379/3")
    assert seen == {"url": "redis://example:6379/3", "decode_responses": True}
    assert sf._redis is not None
