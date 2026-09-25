"""Tests for the Supermemory provider.

The Supermemory SDK is synchronous: every call used to run directly inside the
provider's ``async def`` methods, blocking the event loop for a network
round-trip — and the client carried no timeout, so an unresponsive endpoint
hung the loop indefinitely. The provider now offloads each SDK call to a
worker thread and passes the configured timeout/retry budget to the client.
"""

from __future__ import annotations

import sys
import threading
import types

from pydantic import SecretStr

from core.config.memory import SupermemoryConfig
from core.memory.types import MemoryItem, MemoryType


def test_config_exposes_timeout_and_retry_knobs():
    config = SupermemoryConfig(
        api_key=SecretStr("k"), timeout_seconds=3.5, max_retries=1
    )
    assert config.timeout_seconds == 3.5
    assert config.max_retries == 1


def test_config_timeout_defaults_are_bounded():
    config = SupermemoryConfig()
    assert config.timeout_seconds == 10.0
    assert config.max_retries == 2


class _FakeSearchNS:
    """Stands in for the SDK's ``client.search`` namespace."""

    def __init__(self, owner: _FakeClient) -> None:
        self._owner = owner

    def memories(self, **kwargs):
        self._owner.calls.append(("search.memories", kwargs, threading.get_ident()))
        return types.SimpleNamespace(memories=[])


class _FakeClient:
    """Captures constructor kwargs and the thread each SDK method runs on."""

    ctor_kwargs: dict = {}
    reject_timeout_kwargs = False

    def __init__(self, **kwargs):
        if type(self).reject_timeout_kwargs and (
            "timeout" in kwargs or "max_retries" in kwargs
        ):
            raise TypeError("unexpected keyword argument 'timeout'")
        type(self).ctor_kwargs = kwargs
        self.calls: list = []
        self.search = _FakeSearchNS(self)

    def add(self, **kwargs):
        self.calls.append(("add", kwargs, threading.get_ident()))


def _install_fake_sdk(monkeypatch, *, reject_timeout: bool = False) -> None:
    _FakeClient.ctor_kwargs = {}
    _FakeClient.reject_timeout_kwargs = reject_timeout
    module = types.ModuleType("supermemory")
    module.Supermemory = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "supermemory", module)


def _make_provider(monkeypatch, **config_kwargs):
    from core.memory.supermemory_provider import SupermemoryProvider

    config = SupermemoryConfig(api_key=SecretStr("k"), **config_kwargs)
    return SupermemoryProvider(container_tag="t1", config=config)


def test_client_receives_timeout_and_retries(monkeypatch):
    _install_fake_sdk(monkeypatch)
    _make_provider(monkeypatch, timeout_seconds=3.5, max_retries=1)
    assert _FakeClient.ctor_kwargs["timeout"] == 3.5
    assert _FakeClient.ctor_kwargs["max_retries"] == 1
    assert _FakeClient.ctor_kwargs["api_key"] == "k"


def test_older_sdk_without_timeout_kwargs_still_builds(monkeypatch):
    _install_fake_sdk(monkeypatch, reject_timeout=True)
    provider = _make_provider(monkeypatch)
    assert "timeout" not in _FakeClient.ctor_kwargs
    assert provider._client is not None


async def test_add_runs_sdk_call_off_the_event_loop(monkeypatch):
    _install_fake_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    item = MemoryItem(content="hello", memory_type=MemoryType.LONG_TERM)

    await provider.add(item)

    (name, kwargs, thread_ident) = provider._client.calls[0]
    assert name == "add"
    assert kwargs["content"] == "hello"
    # The sync SDK call must run in a worker thread, never on the loop thread.
    assert thread_ident != threading.get_ident()


async def test_search_runs_off_loop_and_maps_results(monkeypatch):
    _install_fake_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)

    results = await provider.search("query")

    assert results == []
    (name, _kwargs, thread_ident) = provider._client.calls[0]
    assert name == "search.memories"
    assert thread_ident != threading.get_ident()


# ---------------------------------------------------------------------------
# Round-trip: what add() writes, get()/delete()/search()/clear() can reach.
# ---------------------------------------------------------------------------


class _Store:
    """In-memory Supermemory stand-in honouring container tags and filters."""

    def __init__(self) -> None:
        self.rows: list[types.SimpleNamespace] = []
        self.search_calls: list[dict] = []
        self.deleted_containers: list[str] = []
        self._next = 0

    def add(self, *, content, container_tag, metadata):
        self._next += 1
        self.rows.append(
            types.SimpleNamespace(
                id=f"sm-{self._next}",
                content=content,
                tag=container_tag,
                metadata=dict(metadata),
                score=0.9,
                forgotten=False,
            )
        )

    def search_memories(self, *, q, container_tag, limit, filters=None):
        self.search_calls.append(
            {"q": q, "container_tag": container_tag, "limit": limit, "filters": filters}
        )
        conds = (filters or {}).get("AND", [])
        hits = [
            r
            for r in self.rows
            if r.tag == container_tag
            and not r.forgotten
            and all(r.metadata.get(c["key"]) == c["value"] for c in conds)
        ]
        # v4 SDK shape: hits under ``results``.
        return types.SimpleNamespace(results=hits[:limit])

    def forget(self, *, id):
        for r in self.rows:
            if r.id == id:
                r.forgotten = True

    def delete_by_container(self, *, container_tag):
        self.deleted_containers.append(container_tag)
        for r in self.rows:
            if r.tag == container_tag:
                r.forgotten = True


def _install_store_sdk(monkeypatch) -> _Store:
    store = _Store()

    class _Client:
        def __init__(self, **kwargs):
            self.add = store.add
            self.search = types.SimpleNamespace(memories=store.search_memories)
            self.memories = types.SimpleNamespace(
                forget=store.forget, delete_by_container=store.delete_by_container
            )
            self.documents = types.SimpleNamespace(
                delete_by_container=lambda **kw: None
            )

    module = types.ModuleType("supermemory")
    module.Supermemory = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "supermemory", module)
    return store


async def test_add_writes_to_the_tag_reads_use(monkeypatch):
    store = _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    await provider.add(MemoryItem(content="dark mode", memory_type=MemoryType.ENTITY))
    assert store.rows[0].tag == "t1"
    assert store.rows[0].metadata["memory_type"] == MemoryType.ENTITY.value


async def test_get_finds_a_typed_memory_by_id(monkeypatch):
    store = _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    item = MemoryItem(content="dark mode", memory_type=MemoryType.ENTITY)
    other = MemoryItem(content="unrelated", memory_type=MemoryType.EPISODIC)
    await provider.add(other)
    await provider.add(item)

    found = await provider.get(str(item.id))

    assert found is not None
    assert found.content == "dark mode"
    assert found.memory_type == MemoryType.ENTITY
    assert store.search_calls[-1]["filters"] == {
        "AND": [{"key": "id", "value": str(item.id)}]
    }


async def test_get_unknown_id_returns_none(monkeypatch):
    _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    await provider.add(MemoryItem(content="x", memory_type=MemoryType.LONG_TERM))
    assert await provider.get("missing") is None


async def test_delete_forgets_the_matching_memory(monkeypatch):
    store = _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    item = MemoryItem(content="forget me", memory_type=MemoryType.EPISODIC)
    await provider.add(item)

    assert await provider.delete(str(item.id)) is True
    assert store.rows[0].forgotten is True
    assert await provider.get(str(item.id)) is None
    assert await provider.delete(str(item.id)) is False


async def test_untyped_search_spans_every_type(monkeypatch):
    _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    await provider.add(MemoryItem(content="a", memory_type=MemoryType.ENTITY))
    await provider.add(MemoryItem(content="b", memory_type=MemoryType.EPISODIC))

    results = await provider.search("anything", limit=10)
    assert {r.content for r in results} == {"a", "b"}


async def test_typed_search_filters_on_memory_type(monkeypatch):
    store = _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    await provider.add(MemoryItem(content="a", memory_type=MemoryType.ENTITY))
    await provider.add(MemoryItem(content="b", memory_type=MemoryType.EPISODIC))

    results = await provider.search("anything", memory_type=MemoryType.ENTITY)
    assert [r.content for r in results] == ["a"]
    assert store.search_calls[-1]["filters"] == {
        "AND": [{"key": "memory_type", "value": MemoryType.ENTITY.value}]
    }


async def test_caller_metadata_cannot_override_id_or_type(monkeypatch):
    store = _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    item = MemoryItem(
        content="x",
        memory_type=MemoryType.ENTITY,
        metadata={"id": "spoofed", "memory_type": "long_term"},
    )
    await provider.add(item)
    assert store.rows[0].metadata["id"] == str(item.id)
    assert store.rows[0].metadata["memory_type"] == MemoryType.ENTITY.value


async def test_typed_clear_forgets_only_that_type(monkeypatch):
    _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    await provider.add(MemoryItem(content="a", memory_type=MemoryType.ENTITY))
    await provider.add(MemoryItem(content="b", memory_type=MemoryType.EPISODIC))

    await provider.clear(MemoryType.ENTITY)

    remaining = await provider.search("anything", limit=10)
    assert [r.content for r in remaining] == ["b"]


async def test_untyped_clear_sweeps_container_and_legacy_subtags(monkeypatch):
    store = _install_store_sdk(monkeypatch)
    provider = _make_provider(monkeypatch)
    await provider.add(MemoryItem(content="a", memory_type=MemoryType.ENTITY))

    await provider.clear()

    assert "t1" in store.deleted_containers
    assert "t1_entity" in store.deleted_containers
    assert await provider.search("anything", limit=10) == []
