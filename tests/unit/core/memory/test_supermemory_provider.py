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
