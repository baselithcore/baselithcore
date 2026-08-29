"""Per-process cap on concurrent LLM provider calls.

Token budgets and per-minute rate limits bound spend per request/minute, but
nothing bounded how many provider calls were simultaneously in flight in one
process. ``LLM_MAX_CONCURRENT_REQUESTS`` (0 = unlimited, the default) arms a
semaphore around every provider round-trip.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from core.config.services import LLMConfig
from core.services.llm.service import LLMService


class _SlowProvider:
    def __init__(self) -> None:
        self.live = 0
        self.peak = 0

    async def generate(self, **kwargs):
        self.live += 1
        self.peak = max(self.peak, self.live)
        await asyncio.sleep(0.02)
        self.live -= 1
        return ("ok", 1)

    async def close(self) -> None:
        pass


def _service(max_concurrent: int) -> tuple[LLMService, _SlowProvider]:
    provider = _SlowProvider()
    config = LLMConfig(
        provider="ollama", model="llama3.2", max_concurrent_requests=max_concurrent
    )
    with patch.object(LLMService, "_create_provider", return_value=provider):
        service = LLMService(config=config, enable_cache=False)
    return service, provider


async def test_cap_bounds_in_flight_provider_calls():
    service, provider = _service(max_concurrent=1)
    await asyncio.gather(*(service.generate_response(f"prompt-{i}") for i in range(3)))
    assert provider.peak == 1


async def test_zero_means_unlimited_default():
    service, provider = _service(max_concurrent=0)
    await asyncio.gather(*(service.generate_response(f"prompt-{i}") for i in range(3)))
    assert provider.peak == 3


def test_config_default_is_unlimited():
    assert LLMConfig(provider="ollama", model="m").max_concurrent_requests == 0


async def test_cap_covers_streaming_for_the_whole_stream(monkeypatch):
    """An open stream occupies the provider like an in-flight call: the slot
    is held from open to exhaustion, not just for the open."""
    import core.services.llm._streaming as streaming_module

    service, _provider = _service(max_concurrent=1)
    state = {"live": 0, "peak": 0}

    async def fake_open_stream(svc, prompt, model, kwargs):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])

        async def chunks():
            await asyncio.sleep(0.02)
            yield ("chunk", 1)
            state["live"] -= 1

        return chunks(), None, svc.config.provider, model

    monkeypatch.setattr(streaming_module, "open_stream", fake_open_stream)

    async def consume(i: int) -> None:
        async for _ in service.generate_response_stream(f"stream-{i}"):
            pass

    await asyncio.gather(*(consume(i) for i in range(3)))
    assert state["peak"] == 1
