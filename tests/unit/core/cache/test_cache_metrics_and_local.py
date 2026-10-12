"""Tests for cache metrics, the in-memory TTL cache edge cases and the shims."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from baselith.core.cache import local_cache, metrics
from baselith.core.cache.local_cache import TTLCache
from baselith.core.cache.metrics import (
    CacheMetrics,
    CacheMetricsCollector,
    get_metrics_collector,
)


class TestCacheMetrics:
    def test_rates_are_zero_without_traffic(self) -> None:
        m = CacheMetrics()
        assert m.hit_rate == 0.0
        assert m.miss_rate == 1.0
        assert m.avg_ttl_seconds == 0.0
        assert m.total_requests == 0

    def test_rates_and_average_ttl(self) -> None:
        m = CacheMetrics()
        for _ in range(3):
            m.record_hit()
        m.record_miss()
        m.record_set(ttl_seconds=10)
        m.record_set(ttl_seconds=30)
        m.record_set(ttl_seconds=None)  # counted as a set, not in the TTL mean
        m.record_set(ttl_seconds=0)
        assert m.hit_rate == 0.75
        assert m.miss_rate == pytest.approx(0.25)
        assert m.total_requests == 4
        assert m.sets == 4
        assert m.avg_ttl_seconds == 20.0

    def test_update_size_tracks_high_water_mark(self) -> None:
        m = CacheMetrics()
        m.update_size(5)
        m.update_size(2)
        assert (m.current_size, m.max_size_seen) == (2, 5)

    def test_reset_clears_counters_but_keeps_high_water_mark(self) -> None:
        m = CacheMetrics()
        m.record_hit()
        m.record_miss()
        m.record_delete()
        m.record_eviction()
        m.record_set(ttl_seconds=5)
        m.update_size(9)
        m.reset()
        assert m.to_dict() == {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
            "evictions": 0,
            "hit_rate": 0.0,
            "miss_rate": 1.0,
            "total_requests": 0,
            "avg_ttl_seconds": 0.0,
            "current_size": 0,
            "max_size_seen": 9,
        }
        assert m.last_reset_at is not None

    def test_str_summary(self) -> None:
        m = CacheMetrics()
        m.record_hit()
        m.record_miss()
        m.update_size(3)
        assert str(m) == "CacheMetrics(hit_rate=50.00%, requests=2, size=3/3)"


class TestCacheMetricsCollector:
    def test_get_metrics_does_not_create(self) -> None:
        c = CacheMetricsCollector()
        assert c.get_metrics("absent") is None
        created = c.get_or_create_metrics("present")
        assert c.get_metrics("present") is created
        assert c.get_or_create_metrics("present") is created

    def test_reset_unknown_cache_is_a_no_op(self) -> None:
        c = CacheMetricsCollector()
        c.reset_metrics("absent")
        assert c.get_all_metrics() == {}

    def test_reset_single_and_all(self) -> None:
        c = CacheMetricsCollector()
        a = c.get_or_create_metrics("a")
        b = c.get_or_create_metrics("b")
        a.record_hit()
        b.record_hit()
        c.reset_metrics("a")
        assert (a.hits, b.hits) == (0, 1)
        c.reset_all()
        assert b.hits == 0

    def test_summary_aggregates_across_caches(self) -> None:
        c = CacheMetricsCollector()
        assert c.get_summary()["overall_hit_rate"] == 0.0
        a = c.get_or_create_metrics("a")
        b = c.get_or_create_metrics("b")
        a.record_hit()
        a.record_hit()
        a.record_eviction()
        a.update_size(4)
        b.record_miss()
        b.update_size(1)
        assert c.get_summary() == {
            "total_caches": 2,
            "total_hits": 2,
            "total_misses": 1,
            "total_requests": 3,
            "overall_hit_rate": pytest.approx(2 / 3),
            "total_evictions": 1,
            "total_size": 5,
        }
        assert set(c.get_all_metrics()) == {"a", "b"}
        assert c.get_all_metrics()["a"]["hits"] == 2

    def test_global_collector_is_a_lazy_singleton(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(metrics, "_global_collector", None)
        first = get_metrics_collector()
        assert get_metrics_collector() is first


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Drive ``local_cache``'s clock by hand (``now[0]``)."""
    now = [1_000.0]
    monkeypatch.setattr(local_cache, "time", SimpleNamespace(time=lambda: now[0]))
    return now


def _ttl_cache(**kwargs: float) -> TTLCache[str, int]:
    return TTLCache(metrics_name=f"test-{uuid.uuid4().hex}", **kwargs)  # type: ignore[arg-type]


class TestTTLCacheEdges:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [({"maxsize": 0, "ttl": 1}, "maxsize"), ({"maxsize": 1, "ttl": 0}, "ttl")],
    )
    def test_rejects_non_positive_limits(
        self, kwargs: dict[str, float], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            _ttl_cache(**kwargs)

    async def test_overwriting_a_key_refreshes_recency_without_eviction(
        self, clock: list[float]
    ) -> None:
        cache = _ttl_cache(maxsize=2, ttl=10)
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("a", 3)  # "a" becomes most-recent, nothing evicted
        await cache.set("c", 4)  # evicts LRU "b"
        assert await cache.get_many(["a", "b", "c"]) == [3, None, 4]
        assert cache._metrics.evictions == 1

    async def test_batch_ops_on_empty_input(self) -> None:
        cache = _ttl_cache(maxsize=2, ttl=10)
        assert await cache.get_many([]) == []
        await cache.set_many([])
        assert cache._metrics.sets == 0

    async def test_get_many_drops_expired_entries(self, clock: list[float]) -> None:
        cache = _ttl_cache(maxsize=5, ttl=10)
        await cache.set_many([("a", 1), ("b", 2)])
        clock[0] += 11
        assert await cache.get_many(["a", "b"]) == [None, None]
        assert cache._store == {}
        assert cache._metrics.misses == 2

    async def test_set_many_overwrites_and_evicts(self, clock: list[float]) -> None:
        cache = _ttl_cache(maxsize=2, ttl=10)
        await cache.set_many([("a", 1), ("b", 2), ("a", 10), ("c", 3)])
        assert list(cache._store) == ["a", "c"]
        assert await cache.get("a") == 10
        assert cache._metrics.evictions == 1

    async def test_delete_of_missing_key_records_nothing(self) -> None:
        cache = _ttl_cache(maxsize=2, ttl=10)
        await cache.delete("ghost")
        assert cache._metrics.deletes == 0

    async def test_len_purges_expired_entries_on_interval(
        self, clock: list[float]
    ) -> None:
        cache = _ttl_cache(maxsize=5, ttl=10)
        await cache.set("a", 1)
        clock[0] += 5
        await cache.set("b", 2)
        clock[0] += 6  # "a" expired, "b" still live
        cache.PURGE_INTERVAL = 1.0
        assert len(cache) == 1
        assert list(cache._store) == ["b"]


class TestCompatibilityShims:
    def test_ttl_cache_module_re_exports_the_package_classes(self) -> None:
        import baselith.core.cache as pkg
        from baselith.core.cache import ttl_cache

        for name in ttl_cache.__all__:
            assert getattr(ttl_cache, name) is getattr(pkg, name)

    def test_semantic_cache_factory_builds_and_validates(self) -> None:
        from baselith.core.cache import SemanticLLMCache
        from baselith.core.cache.semantic_cache import (
            SemanticLLMCache as RealSemanticLLMCache,
        )

        cache = SemanticLLMCache(maxsize=3, ttl=5, threshold=0.5, embedder=object())
        assert isinstance(cache, RealSemanticLLMCache)

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"maxsize": 0}, "maxsize"),
            ({"ttl": -1}, "ttl"),
            ({"threshold": 1.5}, "threshold"),
            ({"fingerprint_threshold": -0.1}, "fingerprint_threshold"),
        ],
    )
    def test_semantic_cache_rejects_invalid_settings(
        self, kwargs: dict[str, float], message: str
    ) -> None:
        from baselith.core.cache import SemanticLLMCache

        with pytest.raises(ValueError, match=message):
            SemanticLLMCache(embedder=object(), **kwargs)
