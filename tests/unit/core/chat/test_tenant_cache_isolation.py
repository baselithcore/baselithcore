"""Tenant isolation of the chat response cache (key + Redis keyspace).

The response cache used to key on ``(normalized_query, context_hash)`` only.
Two tenants asking the same question over the same retrieved context (a shared
corpus, or an empty one plus identical history) collided on one entry, so one
tenant's generated answer was served to another. The Redis keyspace had the
same hole one level down: every tenant wrote under one prefix, so ``clear()``
flushed all of them.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.chat.agent_state import AgentState
from core.chat.workflow_retrieval import RetrievalPipeline
from core.context import (
    TenantContextError,
    reset_tenant_context,
    set_tenant_context,
)
from core.models.chat import ChatRequest


@pytest.fixture
def service() -> MagicMock:
    svc = MagicMock()
    svc.FINAL_TOP_K = 2
    svc.newline = "\n"
    svc.double_newline = "\n\n"
    svc.section_separator = "---"
    svc.response_cache = AsyncMock()
    svc.response_cache.get = AsyncMock(return_value=None)
    return svc


def _state(query: str = "same question") -> AgentState:
    state = AgentState(request=ChatRequest(query=query))
    state.normalized_query = query
    state.context = "shared corpus chunk"
    return state


async def _key_for_tenant(service: MagicMock, tenant: str) -> tuple[str, ...] | None:
    token = set_tenant_context(tenant)
    try:
        pipeline = RetrievalPipeline(service)
        state = _state()
        await pipeline.check_cache(state)
        return state.cache_key
    finally:
        reset_tenant_context(token)


async def test_cache_key_carries_the_tenant(service):
    """The key's first element is the request tenant."""
    key = await _key_for_tenant(service, "acme")

    assert key is not None
    assert key[0] == "acme"
    assert key[1] == "same question"


async def test_two_tenants_never_share_a_cache_entry(service):
    """Identical query + identical context must still yield distinct keys."""
    acme = await _key_for_tenant(service, "acme")
    globex = await _key_for_tenant(service, "globex")

    assert acme != globex


async def test_cache_is_skipped_when_no_tenant_is_bound(service, monkeypatch):
    """An unbound tenant withholds the key instead of sharing a bucket."""
    monkeypatch.setattr(
        "core.chat.mixins.retrieval_context.get_current_tenant_id",
        MagicMock(side_effect=TenantContextError("no tenant bound")),
    )
    pipeline = RetrievalPipeline(service)
    state = _state()

    await pipeline.check_cache(state)

    assert state.cache_key is None
    service.response_cache.get.assert_not_awaited()
    # The pipeline still proceeds to generation rather than failing the request.
    assert state.next_action in {"plan_backlog", "generate_answer"}


async def test_unexpected_tenant_lookup_failure_is_not_swallowed(service, monkeypatch):
    """Only TenantContextError degrades to a cache skip; a bug must surface."""
    monkeypatch.setattr(
        "core.chat.mixins.retrieval_context.get_current_tenant_id",
        MagicMock(side_effect=RuntimeError("accessor is broken")),
    )
    pipeline = RetrievalPipeline(service)

    with pytest.raises(RuntimeError, match="accessor is broken"):
        await pipeline.check_cache(_state())


@pytest.fixture
def local_backend(monkeypatch) -> None:
    """Force the default (``local``) cache backend."""
    from core.chat import dependencies

    monkeypatch.setattr(dependencies, "CACHE_BACKEND", "local")


async def test_local_backend_is_tenant_scoped_too(local_backend):
    """The in-process backend is the DEFAULT — it must isolate like Redis does.

    Chat history is keyed on the client-supplied ``conversation_id``
    (``core/services/chat/utils/history.py``), so an unscoped process-wide
    ``TTLCache`` handed one tenant another's conversation by guessing an id.
    """
    from core.chat.dependencies import _build_cache

    cache = _build_cache(16, 60.0, namespace="history")
    conversation_id = "conv-42"

    token = set_tenant_context("acme")
    try:
        await cache.set(conversation_id, {"turns": ["acme secret"]})
        assert await cache.get(conversation_id) == {"turns": ["acme secret"]}
    finally:
        reset_tenant_context(token)

    token = set_tenant_context("globex")
    try:
        assert await cache.get(conversation_id) is None
    finally:
        reset_tenant_context(token)


async def test_local_backend_clear_is_scoped_to_the_tenant(local_backend):
    from core.chat.dependencies import _build_cache

    cache = _build_cache(16, 60.0, namespace="history")

    for tenant in ("acme", "globex"):
        token = set_tenant_context(tenant)
        try:
            await cache.set("conv-42", {"turns": [tenant]})
        finally:
            reset_tenant_context(token)

    token = set_tenant_context("acme")
    try:
        await cache.clear()
        assert await cache.get("conv-42") is None
        assert len(cache) == 0
    finally:
        reset_tenant_context(token)

    token = set_tenant_context("globex")
    try:
        assert await cache.get("conv-42") == {"turns": ["globex"]}
        assert len(cache) == 1
    finally:
        reset_tenant_context(token)


async def test_local_backend_bulk_ops_are_tenant_scoped(local_backend):
    from core.chat.dependencies import _build_cache

    cache = _build_cache(16, 60.0, namespace="rerank")

    token = set_tenant_context("acme")
    try:
        await cache.set_many([("a", 1), ("b", 2)])
        assert await cache.get_many(["a", "b"]) == [1, 2]
        await cache.delete("a")
        assert await cache.get_many(["a", "b"]) == [None, 2]
    finally:
        reset_tenant_context(token)

    token = set_tenant_context("globex")
    try:
        assert await cache.get_many(["a", "b"]) == [None, None]
    finally:
        reset_tenant_context(token)


class _FakeRedis:
    """Minimal async Redis double recording writes and honouring SCAN globs."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}

    async def setex(self, key: str, _ttl: int, value: bytes) -> None:
        self.store[key] = value

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    def pipeline(self, transaction: bool = True) -> "_FakePipeline":
        return _FakePipeline(self)

    async def scan(self, cursor: int = 0, match: str = "*", count: int = 10):
        import fnmatch

        return 0, [k for k in self.store if fnmatch.fnmatch(k, match)]

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, str]] = []

    def get(self, key: str) -> None:
        self._ops.append(("get", key))

    def pttl(self, key: str) -> None:
        self._ops.append(("pttl", key))

    async def execute(self) -> list:
        out = []
        for op, key in self._ops:
            if op == "get":
                out.append(self._redis.store.get(key))
            else:
                out.append(60_000 if key in self._redis.store else -2)
        return out


@pytest.fixture
def redis_backend(monkeypatch) -> _FakeRedis:
    from core.chat import dependencies

    fake = _FakeRedis()
    monkeypatch.setattr(dependencies, "CACHE_BACKEND", "redis")
    monkeypatch.setattr(dependencies, "CACHE_REDIS_PREFIX", "baselith")
    monkeypatch.setattr(dependencies, "_get_redis_client", lambda: fake)
    return fake


async def test_redis_prefix_is_scoped_to_the_current_tenant(redis_backend):
    from core.chat.dependencies import _build_cache

    cache = _build_cache(16, 60.0, namespace="response")

    token = set_tenant_context("acme")
    try:
        await cache.set(("acme", "q", "h"), "acme answer")
    finally:
        reset_tenant_context(token)

    assert redis_backend.store
    assert all(k.startswith("baselith:acme:response:") for k in redis_backend.store)


async def test_clear_only_flushes_the_current_tenants_namespace(redis_backend):
    from core.chat.dependencies import _build_cache

    cache = _build_cache(16, 60.0, namespace="response")

    for tenant in ("acme", "globex"):
        token = set_tenant_context(tenant)
        try:
            await cache.set((tenant, "q", "h"), f"{tenant} answer")
        finally:
            reset_tenant_context(token)

    token = set_tenant_context("acme")
    try:
        await cache.clear()
    finally:
        reset_tenant_context(token)

    remaining = list(redis_backend.store)
    assert remaining, "globex's entry must survive acme's clear()"
    assert all(k.startswith("baselith:globex:response:") for k in remaining)


async def test_one_tenant_cannot_read_anothers_redis_entry(redis_backend):
    from core.chat.dependencies import _build_cache

    cache = _build_cache(16, 60.0, namespace="response")
    key = ("shared", "q", "h")

    token = set_tenant_context("acme")
    try:
        await cache.set(key, "acme answer")
        assert await cache.get(key) == "acme answer"
    finally:
        reset_tenant_context(token)

    token = set_tenant_context("globex")
    try:
        assert await cache.get(key) is None
    finally:
        reset_tenant_context(token)


async def test_the_key_annotation_matches_what_is_written(service):
    """``AgentState.cache_key`` was left at ``tuple[str, str]`` when the tenant
    was prefixed onto it, and the write site carried a ``type: ignore`` to say
    so. A field whose declared type contradicts its only writer is a lie that
    every later reader has to re-discover."""
    import inspect
    import typing

    from core.chat.agent_state import AgentState as _AgentState
    from core.chat.mixins import retrieval_context

    key = await _key_for_tenant(service, "acme")
    assert key is not None and len(key) == 3

    hints = typing.get_type_hints(_AgentState)
    assert hints["cache_key"] == (tuple[str, ...] | None)

    source = inspect.getsource(retrieval_context.RetrievalContextMixin.check_cache)
    assert "type: ignore" not in source
