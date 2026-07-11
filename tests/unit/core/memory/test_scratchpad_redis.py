"""Redis scratchpad backend: durability semantics, tenant scoping, TTL."""

import pytest

from core.context import reset_tenant_context, set_tenant_context
from core.memory.scratchpad import Scratchpad
from core.memory.scratchpad_redis import RedisScratchpadBackend


class FakeRedis:
    """Minimal sync-redis double: hashes + expire tracking."""

    def __init__(self):
        self.hashes: dict[str, dict[str, str]] = {}
        self.expirations: dict[str, int] = {}

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    def hdel(self, key, *fields):
        bucket = self.hashes.get(key, {})
        for field in fields:
            bucket.pop(field, None)

    def hkeys(self, key):
        return list(self.hashes.get(key, {}).keys())

    def delete(self, key):
        self.hashes.pop(key, None)
        self.expirations.pop(key, None)

    def expire(self, key, ttl):
        self.expirations[key] = ttl


@pytest.fixture
def fake():
    return FakeRedis()


@pytest.fixture
def backend(fake):
    return RedisScratchpadBackend(redis_client=fake, ttl_seconds=3600)


def test_roundtrip_and_sections(backend):
    backend.set("t1", "plan", "step 1")
    backend.set("t1", "notes", "obs")
    assert backend.get("t1", "plan") == "step 1"
    assert backend.list_sections("t1") == ["notes", "plan"]
    backend.delete("t1", "plan")
    assert backend.get("t1", "plan") is None
    backend.clear("t1")
    assert backend.list_sections("t1") == []


def test_sliding_ttl_refreshed_on_write(backend, fake):
    backend.set("t1", "plan", "v1")
    key = next(iter(fake.hashes))
    assert fake.expirations[key] == 3600


def test_zero_ttl_disables_expiry(fake):
    backend = RedisScratchpadBackend(redis_client=fake, ttl_seconds=0)
    backend.set("t1", "plan", "v1")
    assert fake.expirations == {}


def test_tenant_scoped_keys_isolate_data(fake):
    backend = RedisScratchpadBackend(redis_client=fake, ttl_seconds=0)
    token_a = set_tenant_context("tenant-a")
    try:
        backend.set("t1", "plan", "alpha")
    finally:
        reset_tenant_context(token_a)

    token_b = set_tenant_context("tenant-b")
    try:
        # Same thread_id, different tenant: no bleed-through.
        assert backend.get("t1", "plan") is None
        backend.set("t1", "plan", "beta")
        assert backend.get("t1", "plan") == "beta"
    finally:
        reset_tenant_context(token_b)

    token_a = set_tenant_context("tenant-a")
    try:
        assert backend.get("t1", "plan") == "alpha"
    finally:
        reset_tenant_context(token_a)
    assert any(":tenant-a:" in k for k in fake.hashes)
    assert any(":tenant-b:" in k for k in fake.hashes)


def test_facade_caps_apply_over_redis_backend(fake):
    pad = Scratchpad(
        RedisScratchpadBackend(redis_client=fake, ttl_seconds=0),
        max_section_bytes=16,
        max_sections=2,
    )
    pad.update_section("t1", "a", "ok")
    pad.update_section("t1", "b", "ok")
    from core.memory.scratchpad import ScratchpadOverflowError

    with pytest.raises(ScratchpadOverflowError):
        pad.update_section("t1", "c", "third section")
    with pytest.raises(ScratchpadOverflowError):
        pad.update_section("t1", "a", "x" * 32)
    assert "## a" in pad.read_all("t1")


def test_env_ttl(monkeypatch, fake):
    monkeypatch.setenv("BASELITH_SCRATCHPAD_TTL_SECONDS", "120")
    backend = RedisScratchpadBackend(redis_client=fake)
    backend.set("t1", "s", "v")
    assert next(iter(fake.expirations.values())) == 120


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
