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

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

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


class TestBatchedRoundTrips:
    """set() must be ONE pipelined round-trip (HSET+EXPIRE), and read_all one
    HGETALL instead of HKEYS + one HGET per section."""

    def test_set_pipelines_hset_and_expire(self, fake):
        calls: list[str] = []

        class _Pipe:
            def __init__(self, redis):
                self._redis = redis
                self._ops = []

            def hset(self, key, field, value):
                self._ops.append(("hset", key, field, value))
                return self

            def expire(self, key, ttl):
                self._ops.append(("expire", key, ttl))
                return self

            def execute(self):
                calls.append("execute")
                for op in self._ops:
                    getattr(self._redis, op[0])(*op[1:])
                self._ops.clear()

        fake.pipeline = lambda transaction=False: _Pipe(fake)
        backend = RedisScratchpadBackend(redis_client=fake, ttl_seconds=3600)
        backend.set("t1", "plan", "step 1")
        assert calls == ["execute"]  # one round-trip, not HSET then EXPIRE
        assert backend.get("t1", "plan") == "step 1"
        assert fake.expirations  # TTL still applied through the pipeline

    def test_get_all_uses_hgetall(self, fake):
        fake.hgetall_calls = 0

        def hgetall(key):
            fake.hgetall_calls += 1
            return dict(fake.hashes.get(key, {}))

        fake.hgetall = hgetall
        backend = RedisScratchpadBackend(redis_client=fake, ttl_seconds=0)
        backend.set("t1", "plan", "step 1")
        backend.set("t1", "notes", "obs")

        assert backend.get_all("t1") == {"plan": "step 1", "notes": "obs"}
        assert fake.hgetall_calls == 1

    def test_scratchpad_read_all_prefers_get_all(self, fake):
        fake.hgetall_calls = 0

        def hgetall(key):
            fake.hgetall_calls += 1
            return dict(fake.hashes.get(key, {}))

        fake.hgetall = hgetall
        backend = RedisScratchpadBackend(redis_client=fake, ttl_seconds=0)
        pad = Scratchpad(backend=backend)
        pad.update_section("t1", "plan", "step 1")
        pad.update_section("t1", "notes", "obs")

        rendered = pad.read_all("t1")
        assert "## notes" in rendered and "## plan" in rendered
        assert fake.hgetall_calls == 1  # one HGETALL, no per-section HGETs


class _FakeAsyncPipe:
    """Async pipeline double: queues commands, applies them on execute()."""

    def __init__(self, owner: "FakeAsyncRedis"):
        self._owner = owner
        self._ops: list[tuple] = []

    def hset(self, key, field, value):
        self._ops.append(("hset", key, field, value))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        self._owner.pipeline_executions += 1
        for op in self._ops:
            getattr(self._owner._sync, op[0])(*op[1:])
        self._ops.clear()


class FakeAsyncRedis:
    """Minimal async-redis double sharing storage with a FakeRedis."""

    def __init__(self, sync: FakeRedis):
        self._sync = sync
        self.calls: list[str] = []
        self.pipeline_executions = 0

    def pipeline(self, transaction=False):
        return _FakeAsyncPipe(self)

    async def hget(self, key, field):
        self.calls.append("hget")
        return self._sync.hget(key, field)

    async def hset(self, key, field, value):
        self.calls.append("hset")
        self._sync.hset(key, field, value)

    async def hdel(self, key, *fields):
        self.calls.append("hdel")
        self._sync.hdel(key, *fields)

    async def hkeys(self, key):
        self.calls.append("hkeys")
        return self._sync.hkeys(key)

    async def hgetall(self, key):
        self.calls.append("hgetall")
        return self._sync.hgetall(key)

    async def delete(self, key):
        self.calls.append("delete")
        self._sync.delete(key)

    async def expire(self, key, ttl):
        self.calls.append("expire")
        self._sync.expire(key, ttl)


class TestAsyncBackend:
    """The agent loop must never block on a scratchpad round-trip."""

    @pytest.fixture
    def pair(self, fake):
        aredis = FakeAsyncRedis(fake)
        backend = RedisScratchpadBackend(
            redis_client=fake, async_redis_client=aredis, ttl_seconds=3600
        )
        return backend, aredis

    async def test_async_roundtrip_uses_the_async_client(self, pair):
        backend, aredis = pair
        await backend.aset("t1", "plan", "step 1")
        assert await backend.aget("t1", "plan") == "step 1"
        assert await backend.alist("t1") == ["plan"]
        assert await backend.aget_all("t1") == {"plan": "step 1"}
        await backend.adelete("t1", "plan")
        assert await backend.aget("t1", "plan") is None
        await backend.aclear("t1")
        assert await backend.alist("t1") == []
        # The TTL write went through the pipeline (see TestAsyncPipelining);
        # the reads went straight to the client.
        assert aredis.pipeline_executions >= 1
        assert "hgetall" in aredis.calls and "hkeys" in aredis.calls

    async def test_async_write_refreshes_the_sliding_ttl(self, pair, fake):
        backend, _ = pair
        await backend.aset("t1", "plan", "v1")
        key = next(iter(fake.hashes))
        assert fake.expirations[key] == 3600

    async def test_async_zero_ttl_skips_expire(self, fake):
        aredis = FakeAsyncRedis(fake)
        backend = RedisScratchpadBackend(
            redis_client=fake, async_redis_client=aredis, ttl_seconds=0
        )
        await backend.aset("t1", "plan", "v1")
        assert fake.expirations == {}
        assert "expire" not in aredis.calls

    async def test_async_keys_are_tenant_scoped(self, fake):
        aredis = FakeAsyncRedis(fake)
        backend = RedisScratchpadBackend(
            redis_client=fake, async_redis_client=aredis, ttl_seconds=0
        )
        token = set_tenant_context("tenant-a")
        try:
            await backend.aset("t1", "plan", "alpha")
        finally:
            reset_tenant_context(token)
        token = set_tenant_context("tenant-b")
        try:
            assert await backend.aget("t1", "plan") is None
        finally:
            reset_tenant_context(token)

    async def test_sync_methods_still_use_the_sync_client(self, pair, fake):
        backend, aredis = pair
        backend.set("t1", "plan", "sync write")
        assert backend.get("t1", "plan") == "sync write"
        assert aredis.calls == []  # untouched by the synchronous surface

    async def test_falls_back_to_a_thread_without_an_async_client(
        self, fake, monkeypatch
    ):
        """No async client available → offload the sync call, never block."""
        import threading

        backend = RedisScratchpadBackend(redis_client=fake, ttl_seconds=0)
        monkeypatch.setattr(backend, "_resolve_async_client", lambda: None)
        seen: list[int] = []
        original = fake.hset

        def hset(key, field, value):
            seen.append(threading.get_ident())
            original(key, field, value)

        fake.hset = hset
        await backend.aset("t1", "plan", "v")
        assert seen and seen[0] != threading.get_ident()
        assert await backend.aget("t1", "plan") == "v"

    async def test_facade_async_surface_over_redis(self, pair):
        backend, _ = pair
        pad = Scratchpad(backend, max_section_bytes=16, max_sections=2)
        await pad.aupdate_section("t1", "a", "ok")
        assert await pad.aread_section("t1", "a") == "ok"
        assert "## a" in await pad.aread_all("t1")
        from core.memory.scratchpad import ScratchpadOverflowError

        with pytest.raises(ScratchpadOverflowError):
            await pad.aupdate_section("t1", "b", "x" * 32)


class TestAsyncClientIsLoopKeyed:
    """The async client memo must not outlive its event loop.

    ``core.cache.redis_cache.create_redis_client`` keys its shared pool by the
    running loop, precisely because an asyncio Redis connection is bound to
    the loop that opened it. Memoising the client on the backend defeated
    that: a backend reused from a second loop (a worker restarted, a sync
    entry point calling ``asyncio.run`` twice) would hand out a client whose
    pool belongs to a closed loop.
    """

    def test_each_loop_gets_its_own_client(self, fake, monkeypatch):
        import asyncio

        built: list[object] = []

        def _factory(url, decode_responses=False):
            client = FakeAsyncRedis(fake)
            built.append(client)
            return client

        monkeypatch.setattr(
            "core.cache.redis_cache.create_redis_client", _factory, raising=False
        )
        backend = RedisScratchpadBackend(
            redis_client=fake, url="redis://localhost:6379/0", ttl_seconds=0
        )

        async def _write(value: str) -> str | None:
            await backend.aset("t1", "plan", value)
            return await backend.aget("t1", "plan")

        assert asyncio.run(_write("first")) == "first"
        assert asyncio.run(_write("second")) == "second"
        assert len(built) == 2, "the client was reused across two event loops"
        assert built[0] is not built[1]

    async def test_same_loop_reuses_one_client(self, fake, monkeypatch):
        built: list[object] = []

        def _factory(url, decode_responses=False):
            client = FakeAsyncRedis(fake)
            built.append(client)
            return client

        monkeypatch.setattr(
            "core.cache.redis_cache.create_redis_client", _factory, raising=False
        )
        backend = RedisScratchpadBackend(
            redis_client=fake, url="redis://localhost:6379/0", ttl_seconds=0
        )
        await backend.aset("t1", "a", "1")
        await backend.aset("t1", "b", "2")
        assert len(built) == 1

    async def test_injected_client_is_used_on_every_loop(self, fake):
        """An explicitly injected client is the caller's to manage."""
        aredis = FakeAsyncRedis(fake)
        backend = RedisScratchpadBackend(
            redis_client=fake, async_redis_client=aredis, ttl_seconds=0
        )
        await backend.aset("t1", "a", "1")
        assert backend._resolve_async_client() is aredis


class TestAsyncPipelining:
    """``aset`` must be ONE pipelined round-trip (HSET+EXPIRE), like its twin."""

    async def test_aset_pipelines_hset_and_expire(self, fake):
        aredis = FakeAsyncRedis(fake)
        backend = RedisScratchpadBackend(
            redis_client=fake, async_redis_client=aredis, ttl_seconds=3600
        )
        await backend.aset("t1", "plan", "step 1")
        assert aredis.pipeline_executions == 1  # one round-trip, not two
        assert "hset" not in aredis.calls  # went through the pipeline
        assert await backend.aget("t1", "plan") == "step 1"
        assert fake.expirations  # TTL still applied through the pipeline

    async def test_zero_ttl_skips_the_pipeline(self, fake):
        aredis = FakeAsyncRedis(fake)
        backend = RedisScratchpadBackend(
            redis_client=fake, async_redis_client=aredis, ttl_seconds=0
        )
        await backend.aset("t1", "plan", "v")
        assert aredis.pipeline_executions == 0
        assert "hset" in aredis.calls
