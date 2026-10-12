"""Behavioural tests for ``RedisTTLCache`` operations against a mocked client.

Covers the read/write/batch/clear paths and their failure handling: a
corrupt or unreadable entry must degrade to a miss (and be purged), never
raise into the caller.
"""

from __future__ import annotations

import hashlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import orjson
import pytest

from baselith.core.cache import redis_cache
from baselith.core.cache.redis_cache import RedisTTLCache, _json_default

PREFIX = "unit:cache"
TTL = 100


def _client() -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock()
    client.delete = AsyncMock()
    client.mget = AsyncMock(return_value=[])
    client.scan = AsyncMock(return_value=(0, []))
    return client


def _pipeline(results: list[Any]) -> MagicMock:
    pipe = MagicMock()
    pipe.execute = AsyncMock(return_value=results)
    return pipe


def _cache(client: MagicMock, beta: float | None = 0.0) -> RedisTTLCache[Any, Any]:
    return RedisTTLCache(
        client, prefix=PREFIX + ":", default_ttl=TTL, early_refresh_beta=beta
    )


class TestConstruction:
    def test_requires_redis_package(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(redis_cache, "Redis", None)
        with pytest.raises(RuntimeError, match="redis package"):
            RedisTTLCache(MagicMock())

    def test_prefix_trailing_colon_is_stripped(self) -> None:
        assert _cache(_client()).namespace == PREFIX

    def test_ttl_is_floored_at_one_second(self) -> None:
        cache = RedisTTLCache(_client(), prefix="p", default_ttl=0.2)
        assert cache._ttl == 1

    @pytest.mark.parametrize(
        ("raw", "expected"), [("2.5", 2.5), ("-3", 0.0), ("not-a-number", 1.0)]
    )
    def test_xfetch_beta_from_env(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
    ) -> None:
        monkeypatch.setenv("BASELITH_CACHE_XFETCH_BETA", raw)
        assert _cache(_client(), beta=None)._xfetch_beta == expected


class TestSerialization:
    def test_unknown_objects_fall_back_to_str(self) -> None:
        class Opaque:
            def __str__(self) -> str:
                return "opaque-1"

        assert _json_default(Opaque()) == "opaque-1"

    def test_float_like_objects_become_floats(self) -> None:
        class FloatLike:
            def __float__(self) -> float:
                return 1.5

        assert _json_default(FloatLike()) == 1.5

    def test_key_digest_is_stable_and_namespaced(self) -> None:
        cache = _cache(_client())
        expected = hashlib.sha256(
            orjson.dumps({"a": 1, "b": 2}, option=orjson.OPT_SORT_KEYS)
        ).hexdigest()
        assert cache._serialize_key({"b": 2, "a": 1}) == f"{PREFIX}:{expected}"


class TestGet:
    async def test_plain_get_miss_and_hit(self) -> None:
        client = _client()
        cache = _cache(client)
        assert await cache.get("k") is None
        client.get.return_value = orjson.dumps({"v": 1})
        assert await cache.get("k") == {"v": 1}
        client.pipeline.assert_not_called()

    async def test_xfetch_miss(self) -> None:
        client = _client()
        client.pipeline.return_value = _pipeline([None, -2])
        assert await _cache(client, beta=1.0).get("k") is None

    async def test_xfetch_serves_entry_far_from_expiry(self) -> None:
        client = _client()
        client.pipeline.return_value = _pipeline([orjson.dumps([1, 2]), 90_000])
        assert await _cache(client, beta=1.0).get("k") == [1, 2]

    async def test_xfetch_early_refresh_reports_miss(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A tiny draw makes -log(draw) large: the near-expiry entry is
        # treated as a miss so this caller recomputes before the TTL lapses.
        monkeypatch.setattr(redis_cache.random, "random", lambda: 1e-9)
        client = _client()
        client.pipeline.return_value = _pipeline([orjson.dumps("v"), 500])
        assert await _cache(client, beta=1.0).get("k") is None
        client.delete.assert_not_called()

    @pytest.mark.parametrize("pttl", [None, -1, 0, "100"])
    def test_xfetch_ignores_missing_or_non_positive_ttl(self, pttl: Any) -> None:
        assert _cache(_client(), beta=1.0)._xfetch_expired(pttl) is False

    async def test_corrupt_entry_is_deleted_and_reported_as_miss(self) -> None:
        client = _client()
        client.get.return_value = b"{not json"
        cache = _cache(client)
        assert await cache.get("k") is None
        client.delete.assert_awaited_once_with(cache._serialize_key("k"))

    async def test_delete_failure_after_read_error_is_swallowed(self) -> None:
        client = _client()
        client.get.side_effect = ConnectionError("down")
        client.delete.side_effect = ConnectionError("still down")
        assert await _cache(client).get("k") is None


class TestWrites:
    async def test_set_uses_jittered_ttl_and_json_payload(self) -> None:
        client = _client()
        cache = _cache(client)
        await cache.set("k", {"x": 1})
        key, ttl, payload = client.setex.await_args.args
        assert key == cache._serialize_key("k")
        assert TTL <= ttl <= TTL + TTL // 10
        assert orjson.loads(payload) == {"x": 1}

    async def test_delete_targets_serialized_key(self) -> None:
        client = _client()
        cache = _cache(client)
        await cache.delete("k")
        client.delete.assert_awaited_once_with(cache._serialize_key("k"))

    async def test_set_many_empty_is_a_no_op(self) -> None:
        client = _client()
        await _cache(client).set_many([])
        client.pipeline.assert_not_called()

    async def test_set_many_pipelines_every_item(self) -> None:
        client = _client()
        pipe = _pipeline([True, True])
        client.pipeline.return_value = pipe
        cache = _cache(client)
        await cache.set_many([("a", 1), ("b", 2)])
        client.pipeline.assert_called_once_with(transaction=False)
        keys = [c.args[0] for c in pipe.setex.call_args_list]
        assert keys == [cache._serialize_key("a"), cache._serialize_key("b")]
        assert [orjson.loads(c.args[2]) for c in pipe.setex.call_args_list] == [1, 2]
        pipe.execute.assert_awaited_once()


class TestGetMany:
    async def test_empty_keys_skip_the_round_trip(self) -> None:
        client = _client()
        assert await _cache(client).get_many([]) == []
        client.mget.assert_not_called()

    async def test_mget_failure_returns_one_miss_per_key(self) -> None:
        client = _client()
        client.mget.side_effect = TimeoutError()
        assert await _cache(client).get_many(["a", "b", "c"]) == [None, None, None]

    async def test_mixed_hits_misses_and_corrupt_entries_keep_their_slots(
        self,
    ) -> None:
        client = _client()
        client.mget.return_value = [orjson.dumps(1), None, b"\xff", orjson.dumps(4)]
        client.delete.side_effect = ConnectionError("delete failed")
        cache = _cache(client)
        result = await cache.get_many(["a", "b", "c", "d"])
        assert result == [1, None, None, 4]
        client.delete.assert_awaited_once_with(cache._serialize_key("c"))


class TestClear:
    async def test_scans_all_pages_and_deletes_only_non_empty_ones(self) -> None:
        client = _client()
        client.scan.side_effect = [(7, [b"k1", b"k2"]), (3, []), (0, [b"k3"])]
        await _cache(client).clear()
        assert [c.kwargs["cursor"] for c in client.scan.await_args_list] == [0, 7, 3]
        assert all(
            c.kwargs["match"] == f"{PREFIX}:*" for c in client.scan.await_args_list
        )
        assert [c.args for c in client.delete.await_args_list] == [
            (b"k1", b"k2"),
            (b"k3",),
        ]


class TestPoolHelpers:
    def test_create_client_requires_redis(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(redis_cache, "BlockingConnectionPool", None)
        with pytest.raises(RuntimeError, match="redis package"):
            redis_cache.create_redis_client("redis://localhost:6379/0")

    async def test_close_without_redis_leaves_registry_untouched(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sentinel = MagicMock()
        monkeypatch.setattr(redis_cache, "BlockingConnectionPool", None)
        monkeypatch.setitem(redis_cache._shared_pools, (None, "u", False), sentinel)
        await redis_cache.close_redis_pools()
        assert redis_cache._shared_pools[(None, "u", False)] is sentinel

    async def test_close_survives_a_failing_disconnect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad, good = MagicMock(), MagicMock()
        bad.disconnect = AsyncMock(side_effect=RuntimeError("Event loop is closed"))
        good.disconnect = AsyncMock()
        monkeypatch.setattr(
            redis_cache, "_shared_pools", {(1, "a", False): bad, (1, "b", True): good}
        )
        monkeypatch.setattr(redis_cache, "_pool_loops", {})
        await redis_cache.close_redis_pools()
        bad.disconnect.assert_awaited_once()
        good.disconnect.assert_awaited_once()
        assert redis_cache._shared_pools == {}
