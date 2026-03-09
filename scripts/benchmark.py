#!/usr/bin/env python3
"""
Performance Benchmark Script.

Measures system performance metrics including:
- LLM response times (with/without cache)
- Vector search latency
- Memory usage
- Throughput
"""

import statistics
import time
from typing import List, Dict

# Optional memory profiling
try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False


class BenchmarkResult:
    """Container for benchmark results."""

    def __init__(self, name: str, times: List[float]):
        self.name = name
        self.times = times
        self.count = len(times)
        self.mean = statistics.mean(times) if times else 0
        self.median = statistics.median(times) if times else 0
        self.min = min(times) if times else 0
        self.max = max(times) if times else 0
        self.stdev = statistics.stdev(times) if len(times) > 1 else 0

    def __repr__(self):
        return (
            f"{self.name}:\n"
            f"  Count: {self.count}\n"
            f"  Mean: {self.mean * 1000:.2f}ms\n"
            f"  Median: {self.median * 1000:.2f}ms\n"
            f"  Min: {self.min * 1000:.2f}ms\n"
            f"  Max: {self.max * 1000:.2f}ms\n"
            f"  StdDev: {self.stdev * 1000:.2f}ms"
        )


def get_memory_usage() -> Dict[str, float]:
    """Get current memory usage."""
    if not HAS_PSUTIL:
        return {"error": "psutil not installed"}

    process = psutil.Process()
    mem_info = process.memory_info()
    return {
        "rss_mb": mem_info.rss / 1024 / 1024,
        "vms_mb": mem_info.vms / 1024 / 1024,
    }


def benchmark_function(func, iterations: int = 10, warmup: int = 2) -> BenchmarkResult:
    """Benchmark a function."""
    # Warmup
    for _ in range(warmup):
        func()

    # Measure
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return BenchmarkResult(func.__name__, times)


async def benchmark_async_function(
    func, iterations: int = 10, warmup: int = 2
) -> BenchmarkResult:
    """Benchmark an async function."""
    # Warmup
    for _ in range(warmup):
        await func()

    # Measure
    times = []
    for _ in range(iterations):
        start = time.perf_counter()
        await func()
        elapsed = time.perf_counter() - start
        times.append(elapsed)

    return BenchmarkResult(func.__name__, times)


def run_benchmarks():
    """Run all benchmarks."""
    print("=" * 60)
    print("Baselith-Core Performance Benchmark")
    print("=" * 60)
    print()

    # Memory
    if HAS_PSUTIL:
        mem = get_memory_usage()
        print(f"Initial Memory: RSS={mem['rss_mb']:.1f}MB, VMS={mem['vms_mb']:.1f}MB")
        print()

    results = []

    # Import benchmarks
    try:
        from core.config import get_app_config

        def bench_config_load():
            get_app_config()

        results.append(benchmark_function(bench_config_load, iterations=100))
        print(results[-1])
        print()
    except Exception as e:
        print(f"Config benchmark failed: {e}")

    # LLM Service (if available)
    try:
        from core.services.llm import get_llm_service

        def bench_llm_init():
            # Just test service access
            get_llm_service()

        results.append(benchmark_function(bench_llm_init, iterations=50))
        print(results[-1])
        print()
    except Exception as e:
        print(f"LLM Service benchmark skipped: {e}")

    # VectorStore (if available)
    try:
        from core.services.vectorstore import get_vectorstore_service

        def bench_vectorstore_init():
            get_vectorstore_service()

        results.append(benchmark_function(bench_vectorstore_init, iterations=50))
        print(results[-1])
        print()
    except Exception as e:
        print(f"VectorStore benchmark skipped: {e}")

    # Cache operations
    try:
        from core.optimization.caching import get_semantic_cache

        cache = get_semantic_cache()
        test_prompt = "What is the meaning of life?"

        def bench_cache_set():
            cache.cache_response(test_prompt, "42", model="test")

        def bench_cache_get():
            cache.get_response(test_prompt, model="test")

        results.append(benchmark_function(bench_cache_set, iterations=100))
        print(results[-1])
        print()

        results.append(benchmark_function(bench_cache_get, iterations=100))
        print(results[-1])
        print()
    except Exception as e:
        print(f"Cache benchmark skipped: {e}")

    # Final memory
    if HAS_PSUTIL:
        mem = get_memory_usage()
        print(f"Final Memory: RSS={mem['rss_mb']:.1f}MB, VMS={mem['vms_mb']:.1f}MB")

    # Summary
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for r in results:
        print(f"  {r.name}: {r.mean * 1000:.2f}ms (±{r.stdev * 1000:.2f}ms)")

    return results


if __name__ == "__main__":
    run_benchmarks()
