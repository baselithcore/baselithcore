"""The graph's read cache: that it exists, works, and returns rows.

`GraphDb.query` is synchronous and used to reach for the async cache layer
without awaiting it. Under the redis backend that returned a coroutine object
where the caller expected rows; under the memory backend an empty `TTLCache`
is falsy, so the branch was skipped and — because the `set` was never awaited
either — it stayed empty and stayed skipped. Caching was inert in one
configuration and actively wrong in the other, and the existing tests missed
both because they patch the cache with a synchronous `MagicMock`.

These tests use the real cache and assert on what the caller receives.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from core.context import set_tenant_context
from core.graph._cache import SyncTTLCache
from core.graph.core import GraphDb

_ROWS = [["header"], ["row"], ["stats"]]


@pytest.fixture
def db():
    """A GraphDb with a stub client, wired exactly as production wires it."""
    set_tenant_context("acme")
    instance = GraphDb.__new__(GraphDb)
    instance.enabled = True
    instance.graph_name = "g"
    instance._cache_ttl = 60
    instance._cache = None
    instance._cache_initialized = False
    client = MagicMock()
    client.execute_command.return_value = _ROWS
    instance._get_client = lambda: client  # type: ignore[method-assign]
    instance._client = client
    return instance


class TestTheCallerGetsRows:
    def test_a_read_returns_rows_not_a_coroutine(self, db):
        result = db.query("MATCH (n) RETURN n")

        assert not asyncio.iscoroutine(result)
        assert result == _ROWS

    def test_a_cached_read_returns_rows_not_a_coroutine(self, db):
        """The regression: the SECOND read is the one that used to break."""
        db.query("MATCH (n) RETURN n")
        second = db.query("MATCH (n) RETURN n")

        assert not asyncio.iscoroutine(second)
        assert second == _ROWS

    def test_the_cache_actually_serves_the_second_read(self, db):
        db.query("MATCH (n) RETURN n")
        db.query("MATCH (n) RETURN n")

        assert db._client.execute_command.call_count == 1


class TestWhatIsNotCached:
    @pytest.mark.parametrize(
        "cypher",
        [
            "CREATE (n)",
            "MERGE (n)",
            "MATCH (n) SET n.x = 1",
            "MATCH (n) DELETE n",
            "MATCH (n) DETACH DELETE n",
            "MATCH (n) REMOVE n.x",
            "DROP INDEX x",
            "CALL db.labels()",
        ],
    )
    def test_writes_always_reach_the_backend(self, db, cypher):
        db.query(cypher)
        db.query(cypher)

        assert db._client.execute_command.call_count == 2

    def test_a_different_tenant_does_not_see_cached_rows(self, db):
        """Tenant id is part of the key; a cross-tenant hit would be a leak."""
        db.query("MATCH (n) RETURN n")
        set_tenant_context("globex")

        db.query("MATCH (n) RETURN n")

        assert db._client.execute_command.call_count == 2


class TestSyncTTLCache:
    def test_stores_and_returns(self):
        cache = SyncTTLCache(maxsize=4, ttl=60)
        cache.set("k", [1, 2])

        assert cache.get("k") == [1, 2]

    def test_a_miss_is_none(self):
        assert SyncTTLCache().get("absent") is None

    def test_an_expired_entry_is_a_miss(self, monkeypatch):
        cache = SyncTTLCache(maxsize=4, ttl=0.01)
        cache.set("k", "v")
        # Capture the real clock first: patching `time.monotonic` with a lambda
        # that calls `time.monotonic` would recurse into the patch.
        later = time.monotonic() + 1
        monkeypatch.setattr("core.graph._cache.time.monotonic", lambda: later)

        assert cache.get("k") is None

    def test_eviction_is_least_recently_used(self):
        cache = SyncTTLCache(maxsize=2, ttl=60)
        cache.set("a", 1)
        cache.set("b", 2)
        cache.get("a")  # touch 'a', making 'b' the least recent
        cache.set("c", 3)

        assert cache.get("a") == 1
        assert cache.get("b") is None
        assert cache.get("c") == 3

    def test_overwriting_a_key_does_not_grow_the_cache(self):
        cache = SyncTTLCache(maxsize=2, ttl=60)
        cache.set("a", 1)
        cache.set("a", 2)

        assert len(cache) == 1
        assert cache.get("a") == 2

    def test_an_empty_cache_is_still_truthy(self):
        """`if self._cache:` must not read an empty cache as "no cache".

        `__len__` without `__bool__` is what made the async TTLCache falsy
        while empty, which silently disabled graph caching entirely.
        """
        cache = SyncTTLCache()

        assert len(cache) == 0
        assert bool(cache) is True

    def test_clear_empties_it(self):
        cache = SyncTTLCache()
        cache.set("k", "v")
        cache.clear()

        assert cache.get("k") is None

    def test_maxsize_must_be_positive(self):
        with pytest.raises(ValueError, match="maxsize"):
            SyncTTLCache(maxsize=0)

    def test_get_and_set_are_not_coroutines(self):
        """The whole point: a synchronous caller gets a value, not an awaitable."""
        cache = SyncTTLCache()

        assert not asyncio.iscoroutinefunction(cache.get)
        assert not asyncio.iscoroutinefunction(cache.set)
