"""Client-side caching driven by the server's `ttlMs` / `cacheScope` hints."""

from __future__ import annotations

import time
from typing import Any

import pytest

from core.mcp.cache import ResultCache, cache_key


def _result(ttl_ms: int = 60000, **extra: Any) -> dict[str, Any]:
    return {
        "resultType": "complete",
        "tools": [{"name": "echo"}],
        "ttlMs": ttl_ms,
        "cacheScope": "public",
        **extra,
    }


class TestKey:
    def test_meta_does_not_change_the_key(self) -> None:
        """Protocol metadata identifies the request, not the answer."""
        assert cache_key("tools/list", {"_meta": {"a": 1}}) == cache_key(
            "tools/list", {"_meta": {"b": 2}}
        )

    def test_salient_params_do(self) -> None:
        assert cache_key("resources/read", {"uri": "a"}) != cache_key(
            "resources/read", {"uri": "b"}
        )

    def test_pages_are_cached_independently(self) -> None:
        assert cache_key("tools/list", {}) != cache_key("tools/list", {"cursor": "x"})


class TestFreshness:
    def test_a_fresh_result_is_served_from_cache(self) -> None:
        cache = ResultCache()
        cache.store("tools/list", {}, _result())

        assert cache.get("tools/list", {}) == _result()

    def test_a_stale_result_is_dropped(self) -> None:
        cache = ResultCache()
        cache.store("tools/list", {}, _result(ttl_ms=1))
        time.sleep(0.005)

        assert cache.get("tools/list", {}) is None
        assert len(cache) == 0

    def test_ttl_zero_means_never_cache(self) -> None:
        """`ttlMs: 0` is the server saying the answer is already stale."""
        cache = ResultCache()
        cache.store("tools/list", {}, _result(ttl_ms=0))

        assert cache.get("tools/list", {}) is None


class TestWhatIsNotCacheable:
    @pytest.mark.parametrize("method", ["tools/call", "prompts/get", "ping"])
    def test_non_cacheable_methods_are_ignored(self, method: str) -> None:
        cache = ResultCache()
        cache.store(method, {}, _result())

        assert len(cache) == 0

    def test_interim_results_are_never_cached(self) -> None:
        cache = ResultCache()
        cache.store(
            "resources/read", {"uri": "a"}, {"resultType": "input_required", "ttlMs": 1}
        )

        assert len(cache) == 0

    def test_round_trip_retries_are_never_cached(self) -> None:
        """Their inputs are not part of the key, so the entry would mislead."""
        cache = ResultCache()
        cache.store(
            "resources/read",
            {"uri": "a", "inputResponses": {"k": {}}, "requestState": "s"},
            _result(),
        )

        assert len(cache) == 0

    def test_a_result_without_hints_is_not_cached(self) -> None:
        """A legacy server sends none; caching it would invent a policy."""
        cache = ResultCache()
        cache.store("tools/list", {}, {"tools": []})

        assert len(cache) == 0


class TestInvalidation:
    def test_list_changed_invalidates_the_matching_listing(self) -> None:
        cache = ResultCache()
        cache.store("tools/list", {}, _result())
        cache.store("prompts/list", {}, _result())

        cache.invalidate("notifications/tools/list_changed")

        assert cache.get("tools/list", {}) is None
        assert cache.get("prompts/list", {}) is not None

    def test_resources_list_changed_invalidates_templates_too(self) -> None:
        cache = ResultCache()
        cache.store("resources/list", {}, _result())
        cache.store("resources/templates/list", {}, _result())

        cache.invalidate("notifications/resources/list_changed")

        assert len(cache) == 0

    def test_an_unrelated_notification_changes_nothing(self) -> None:
        cache = ResultCache()
        cache.store("tools/list", {}, _result())

        cache.invalidate("notifications/progress")

        assert cache.get("tools/list", {}) is not None


class TestClientIntegration:
    @pytest.mark.asyncio
    async def test_a_second_list_does_not_reach_the_server(self) -> None:
        from types import SimpleNamespace

        from core.mcp.client import MCPClient

        calls: list[str] = []
        client = MCPClient()
        client._connected = True
        client.__dict__["_config"] = SimpleNamespace()

        async def send(method: str, params: dict[str, Any]) -> dict[str, Any]:
            calls.append(method)
            result = _result()
            client.cache.store(method, params, result)
            return result

        original = client._send_request

        async def instrumented(method: str, params: dict[str, Any]) -> dict[str, Any]:
            cached = client.cache.get(method, params)
            if cached is not None:
                return cached
            return await send(method, params)

        client._send_request = instrumented  # type: ignore[assignment]
        assert original is not None

        await client.list_tools()
        await client.list_tools()

        assert calls == ["tools/list"]
