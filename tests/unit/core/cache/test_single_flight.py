"""Tests for the per-key single-flight helper."""

from __future__ import annotations

import asyncio

import pytest

from core.cache.single_flight import SingleFlight


@pytest.mark.asyncio
async def test_coalesces_concurrent_calls_for_same_key() -> None:
    sf: SingleFlight[int] = SingleFlight()
    call_count = 0

    async def factory() -> int:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return 42

    results = await asyncio.gather(*[sf.do("k", factory) for _ in range(10)])
    assert results == [42] * 10
    assert call_count == 1
    assert sf.in_flight() == 0


@pytest.mark.asyncio
async def test_distinct_keys_run_independently() -> None:
    sf: SingleFlight[str] = SingleFlight()
    counts: dict[str, int] = {}

    async def factory(key: str) -> str:
        counts[key] = counts.get(key, 0) + 1
        await asyncio.sleep(0.01)
        return key

    results = await asyncio.gather(
        sf.do("a", lambda: factory("a")),
        sf.do("b", lambda: factory("b")),
        sf.do("a", lambda: factory("a")),
    )
    assert sorted(results) == ["a", "a", "b"]
    assert counts == {"a": 1, "b": 1}


@pytest.mark.asyncio
async def test_factory_exception_propagates_to_all_waiters() -> None:
    sf: SingleFlight[int] = SingleFlight()
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    async def call() -> Exception | None:
        try:
            await sf.do("k", factory)
        except RuntimeError as exc:
            return exc
        return None

    results = await asyncio.gather(*[call() for _ in range(5)])
    assert calls == 1
    assert all(isinstance(r, RuntimeError) for r in results)


@pytest.mark.asyncio
async def test_subsequent_call_after_completion_re_runs_factory() -> None:
    sf: SingleFlight[int] = SingleFlight()
    calls = 0

    async def factory() -> int:
        nonlocal calls
        calls += 1
        return calls

    first = await sf.do("k", factory)
    second = await sf.do("k", factory)
    assert first == 1
    assert second == 2
    assert calls == 2
