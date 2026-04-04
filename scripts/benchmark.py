#!/usr/bin/env python3
"""
Performance benchmark script for local hotspot measurements.

Measures repeatable, self-contained benchmarks for:
- cold imports that affect startup latency
- event bus emit hot paths
- local batch cache operations
- Redis client/pool factory overhead
- existing optional service initialization hooks
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

# Optional memory profiling
try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


@dataclass(slots=True)
class BenchmarkResult:
    """Container for benchmark results."""

    name: str
    times: list[float]

    @property
    def count(self) -> int:
        return len(self.times)

    @property
    def mean(self) -> float:
        return statistics.mean(self.times) if self.times else 0.0

    @property
    def median(self) -> float:
        return statistics.median(self.times) if self.times else 0.0

    @property
    def minimum(self) -> float:
        return min(self.times) if self.times else 0.0

    @property
    def maximum(self) -> float:
        return max(self.times) if self.times else 0.0

    @property
    def stdev(self) -> float:
        return statistics.stdev(self.times) if len(self.times) > 1 else 0.0

    @property
    def ops_per_second(self) -> float:
        return (1.0 / self.mean) if self.mean else 0.0

    def summary_line(self) -> str:
        return (
            f"{self.name}: {self.mean * 1000:.2f}ms "
            f"(median {self.median * 1000:.2f}ms, "
            f"ops/s {self.ops_per_second:.1f})"
        )

    def __repr__(self) -> str:
        return (
            f"{self.name}:\n"
            f"  Count: {self.count}\n"
            f"  Mean: {self.mean * 1000:.2f}ms\n"
            f"  Median: {self.median * 1000:.2f}ms\n"
            f"  Min: {self.minimum * 1000:.2f}ms\n"
            f"  Max: {self.maximum * 1000:.2f}ms\n"
            f"  StdDev: {self.stdev * 1000:.2f}ms\n"
            f"  Ops/s: {self.ops_per_second:.1f}"
        )


def get_memory_usage() -> dict[str, float | str]:
    """Get current memory usage."""
    if not HAS_PSUTIL:
        return {"error": "psutil not installed"}

    process = psutil.Process()
    mem_info = process.memory_info()
    return {
        "rss_mb": mem_info.rss / 1024 / 1024,
        "vms_mb": mem_info.vms / 1024 / 1024,
    }


def benchmark_function(
    func: Callable[[], object],
    *,
    name: str,
    iterations: int = 10,
    warmup: int = 2,
) -> BenchmarkResult:
    """Benchmark a synchronous function."""
    for _ in range(warmup):
        func()

    times: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        times.append(time.perf_counter() - start)

    return BenchmarkResult(name=name, times=times)


async def benchmark_async_function(
    func: Callable[[], Awaitable[object]],
    *,
    name: str,
    iterations: int = 10,
    warmup: int = 2,
) -> BenchmarkResult:
    """Benchmark an async function."""
    for _ in range(warmup):
        await func()

    times: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        await func()
        times.append(time.perf_counter() - start)

    return BenchmarkResult(name=name, times=times)


def benchmark_subprocess(
    code: str,
    *,
    name: str,
    iterations: int = 5,
    warmup: int = 1,
) -> BenchmarkResult:
    """Benchmark a cold Python subprocess execution."""
    command = [sys.executable, "-c", code]

    def run_once() -> None:
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    return benchmark_function(run_once, name=name, iterations=iterations, warmup=warmup)


def print_result(result: BenchmarkResult) -> None:
    """Print a single benchmark result."""
    print(result)
    print()


async def run_async_benchmarks(quick: bool) -> list[BenchmarkResult]:
    """Run async-only benchmarks."""
    results: list[BenchmarkResult] = []
    async_iterations = 120 if quick else 400
    async_warmup = 10 if quick else 25

    try:
        from core.events import EventBus

        async def noop_handler(_data: dict[str, object]) -> None:
            return None

        direct_bus = EventBus(enable_wildcards=True)
        direct_bus.subscribe("bench.direct", noop_handler)

        async def bench_event_bus_direct_cached() -> None:
            await direct_bus.emit("bench.direct", {"value": 1})

        async def bench_event_bus_direct_uncached() -> None:
            direct_bus._invalidate_handler_cache()
            await direct_bus.emit("bench.direct", {"value": 1})

        wildcard_bus = EventBus(enable_wildcards=True)
        wildcard_bus.subscribe("bench.*", noop_handler)

        async def bench_event_bus_wildcard_cached() -> None:
            await wildcard_bus.emit("bench.match", {"value": 1})

        async def bench_event_bus_wildcard_uncached() -> None:
            wildcard_bus._invalidate_handler_cache()
            await wildcard_bus.emit("bench.match", {"value": 1})

        for name, func in [
            ("event_bus_emit_direct_cached", bench_event_bus_direct_cached),
            ("event_bus_emit_direct_uncached", bench_event_bus_direct_uncached),
            ("event_bus_emit_wildcard_cached", bench_event_bus_wildcard_cached),
            ("event_bus_emit_wildcard_uncached", bench_event_bus_wildcard_uncached),
        ]:
            result = await benchmark_async_function(
                func,
                name=name,
                iterations=async_iterations,
                warmup=async_warmup,
            )
            results.append(result)
            print_result(result)
    except Exception as exc:
        print(f"Event bus benchmarks skipped: {exc}")

    try:
        from core.cache.local_cache import TTLCache

        cache = TTLCache(maxsize=512, ttl=60, metrics_name="benchmark_local_cache")
        items = [(f"bench:{idx}", {"value": idx}) for idx in range(64)]
        keys = [key for key, _ in items]
        await cache.set_many(items)

        async def bench_local_cache_scalar_reads() -> None:
            for key in keys:
                await cache.get(key)

        async def bench_local_cache_batch_reads() -> None:
            await cache.get_many(keys)

        async def bench_local_cache_scalar_writes() -> None:
            for key, value in items:
                await cache.set(key, value)

        async def bench_local_cache_batch_writes() -> None:
            await cache.set_many(items)

        for name, func in [
            ("local_cache_scalar_reads", bench_local_cache_scalar_reads),
            ("local_cache_batch_reads", bench_local_cache_batch_reads),
            ("local_cache_scalar_writes", bench_local_cache_scalar_writes),
            ("local_cache_batch_writes", bench_local_cache_batch_writes),
        ]:
            result = await benchmark_async_function(
                func,
                name=name,
                iterations=async_iterations,
                warmup=async_warmup,
            )
            results.append(result)
            print_result(result)
    except Exception as exc:
        print(f"Local cache benchmarks skipped: {exc}")

    try:
        from core.optimization.caching import get_semantic_cache

        semantic_cache = get_semantic_cache()
        prompt = "What is the meaning of life?"

        async def bench_semantic_cache_set() -> None:
            await semantic_cache.cache_response(prompt, "42", model="benchmark")

        async def bench_semantic_cache_get() -> None:
            await semantic_cache.get_response(prompt, model="benchmark")

        for name, func in [
            ("semantic_cache_set", bench_semantic_cache_set),
            ("semantic_cache_get", bench_semantic_cache_get),
        ]:
            result = await benchmark_async_function(
                func,
                name=name,
                iterations=20 if quick else 75,
                warmup=2,
            )
            results.append(result)
            print_result(result)
    except Exception as exc:
        print(f"Semantic cache benchmarks skipped: {exc}")

    try:
        from core.cache import close_redis_pools, create_redis_client

        redis_url = "redis://localhost:6379/0"

        async def bench_redis_client_factory_cold() -> None:
            await close_redis_pools()
            client = create_redis_client(redis_url)
            await client.aclose()
            await close_redis_pools()

        warm_client = create_redis_client(redis_url)
        await warm_client.aclose()

        async def bench_redis_client_factory_warm() -> None:
            client = create_redis_client(redis_url)
            await client.aclose()

        for name, func in [
            ("redis_client_factory_cold", bench_redis_client_factory_cold),
            ("redis_client_factory_warm", bench_redis_client_factory_warm),
        ]:
            result = await benchmark_async_function(
                func,
                name=name,
                iterations=20 if quick else 80,
                warmup=1,
            )
            results.append(result)
            print_result(result)

        await close_redis_pools()
    except Exception as exc:
        print(f"Redis factory benchmarks skipped: {exc}")

    return results


def run_benchmarks(*, quick: bool = False) -> list[BenchmarkResult]:
    """Run all benchmarks."""
    print("=" * 60)
    print("Baselith-Core Performance Benchmark")
    print("=" * 60)
    print()

    if HAS_PSUTIL:
        mem = get_memory_usage()
        print(f"Initial Memory: RSS={mem['rss_mb']:.1f}MB, VMS={mem['vms_mb']:.1f}MB")
        print()

    results: list[BenchmarkResult] = []
    sync_iterations = 25 if quick else 100

    try:
        from core.config import get_app_config

        result = benchmark_function(
            get_app_config,
            name="config_load",
            iterations=sync_iterations,
            warmup=5,
        )
        results.append(result)
        print_result(result)
    except Exception as exc:
        print(f"Config benchmark failed: {exc}")

    try:
        result = benchmark_subprocess(
            "import core.chat.service",
            name="chat_service_import_cold",
            iterations=3 if quick else 8,
            warmup=0,
        )
        results.append(result)
        print_result(result)
    except Exception as exc:
        print(f"Chat import benchmark skipped: {exc}")

    try:
        from core.services.llm import get_llm_service

        result = benchmark_function(
            get_llm_service,
            name="llm_service_get",
            iterations=10 if quick else 50,
            warmup=2,
        )
        results.append(result)
        print_result(result)
    except Exception as exc:
        print(f"LLM service benchmark skipped: {exc}")

    try:
        from core.services.vectorstore import get_vectorstore_service

        result = benchmark_function(
            get_vectorstore_service,
            name="vectorstore_service_get",
            iterations=10 if quick else 50,
            warmup=2,
        )
        results.append(result)
        print_result(result)
    except Exception as exc:
        print(f"VectorStore benchmark skipped: {exc}")

    results.extend(asyncio.run(run_async_benchmarks(quick)))

    if HAS_PSUTIL:
        mem = get_memory_usage()
        print(f"Final Memory: RSS={mem['rss_mb']:.1f}MB, VMS={mem['vms_mb']:.1f}MB")

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for result in results:
        print(f"  {result.summary_line()}")

    return results


def main() -> list[BenchmarkResult]:
    """CLI entry point."""
    logging.getLogger().setLevel(logging.ERROR)
    try:
        from core.observability.logging import configure_logging

        configure_logging(level="ERROR", stream=sys.stderr)
    except Exception:
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run fewer iterations for a fast local sanity check.",
    )
    args = parser.parse_args()
    return run_benchmarks(quick=args.quick)


if __name__ == "__main__":
    main()
