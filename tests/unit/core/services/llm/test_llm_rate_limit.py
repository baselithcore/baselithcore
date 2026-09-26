"""The opt-in client-side LLM call rate limit (``RESILIENCE_LLM_RATE_*``).

Three properties matter: off means *off* (the limiter is never built, the
provider is reached as before); on, the N+1th call inside a window waits for
a slot and, past the wait bound, fails with a typed rate-limit error before
the provider is contacted; and every generation path — streaming included —
goes through the same gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.config.resilience import ResilienceConfig
from core.resilience.rate_limiter import RateLimitResult
from core.services.llm import rate_limit
from core.services.llm.exceptions import RateLimitError
from core.services.llm.images import GeneratedImage, generate_image
from core.services.llm.message_runtime import generate_messages
from core.services.llm.messages import Message
from core.services.llm.rate_limit import (
    LocalLLMRateLimitError,
    acquire_llm_call_slot,
    llm_rate_limit_key,
    reset_llm_rate_limiter,
)
from core.services.llm.service import LLMService
from core.services.llm.structured import generate_structured
from core.services.llm.tool_calling import LLMResult

pytestmark = [pytest.mark.unit]

MODEL = "gpt-4o-mini"


@pytest.fixture(autouse=True)
def _fresh_limiter():
    reset_llm_rate_limiter()
    yield
    reset_llm_rate_limiter()


@pytest.fixture(autouse=True)
def _isolated_token_ledger():
    """Keep token reports out of the process-wide cost context."""
    from core.middleware.cost_control import _cost_context

    token = _cost_context.set(None)
    try:
        yield
    finally:
        _cost_context.reset(token)


def _configure(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    settings = {"llm_rate_enabled": True, "llm_rate_max_wait": 0.0, **overrides}
    config = ResilienceConfig(**settings)  # type: ignore[arg-type]
    monkeypatch.setattr(rate_limit, "get_resilience_config", lambda: config)
    monkeypatch.setattr(rate_limit, "_redis_declared", lambda: False)


def _service(provider: str = "openai", *, messages: bool = False) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider=provider,
            model=MODEL,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=messages,
            thinking_enabled=False,
        )
        service = LLMService(enable_semantic_cache=False)
    service.provider = SimpleNamespace(
        supports_native_tools=messages, supports_messages=messages
    )
    service.cost_tracker = None
    return service


class TestDisabled:
    async def test_default_is_off(self) -> None:
        assert ResilienceConfig().llm_rate_enabled is False

    async def test_disabled_never_builds_or_checks_a_limiter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_enabled=False, llm_rate_limit=1)
        builder = AsyncMock(side_effect=AssertionError("limiter built"))
        monkeypatch.setattr(rate_limit, "_get_limiter", builder)

        for _ in range(5):
            await acquire_llm_call_slot("openai")

        assert not builder.called

    async def test_disabled_text_path_reaches_the_provider_every_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_enabled=False, llm_rate_limit=1)
        service = _service()
        service._generate_with_retry = AsyncMock(return_value=("hi", 10))

        for i in range(3):
            await service.generate_response(f"q{i}")

        assert service._generate_with_retry.await_count == 3


class TestEnabled:
    async def test_the_call_over_the_limit_fails_fast_when_max_wait_is_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=2, llm_rate_window=60)

        await acquire_llm_call_slot("openai")
        await acquire_llm_call_slot("openai")
        with pytest.raises(LocalLLMRateLimitError) as info:
            await acquire_llm_call_slot("openai")

        err = info.value
        assert isinstance(err, RateLimitError)
        assert err.status_code is None
        assert err.key == "llm:openai"
        assert err.retry_after is not None and 0 < err.retry_after <= 60

    async def test_the_call_over_the_limit_waits_for_a_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=1, llm_rate_max_wait=5.0)
        answers = iter(
            [
                RateLimitResult(
                    allowed=False, remaining=0, reset_at=0, retry_after=1.5
                ),
                RateLimitResult(allowed=True, remaining=0, reset_at=0),
            ]
        )
        limiter = SimpleNamespace(check=lambda key: next(answers), limit=1, window=60)
        monkeypatch.setattr(
            rate_limit,
            "_get_limiter",
            AsyncMock(return_value=rate_limit._Limiter(limiter, offload=False)),  # type: ignore[arg-type]
        )
        sleep = AsyncMock()
        monkeypatch.setattr(rate_limit.asyncio, "sleep", sleep)

        await acquire_llm_call_slot("openai")

        sleep.assert_awaited_once_with(1.5)

    async def test_a_wait_past_the_bound_raises_without_sleeping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=1, llm_rate_max_wait=1.0)
        sleep = AsyncMock()
        monkeypatch.setattr(rate_limit.asyncio, "sleep", sleep)

        await acquire_llm_call_slot("openai")
        with pytest.raises(LocalLLMRateLimitError):
            await acquire_llm_call_slot("openai")

        assert not sleep.called

    async def test_the_text_path_is_refused_before_the_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=1)
        service = _service()
        service._generate_with_retry = AsyncMock(return_value=("hi", 10))

        await service.generate_response("q1")
        with pytest.raises(LocalLLMRateLimitError):
            await service.generate_response("q2")

        assert service._generate_with_retry.await_count == 1

    async def test_the_streaming_path_takes_a_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=1)
        service = _service()
        opened = AsyncMock()

        await acquire_llm_call_slot("openai")  # window now full
        with patch("core.services.llm._streaming.open_stream", opened):
            with pytest.raises(LocalLLMRateLimitError):
                _ = [c async for c in service.generate_response_stream("q")]

        assert not opened.called


class TestEveryPathAcquires:
    async def test_structured_messages_and_images_await_the_gate(self) -> None:
        gate = AsyncMock()
        service = _service(messages=True)
        result = LLMResult(text="hi", tokens_used=10)

        with (
            patch("core.services.llm.structured.acquire_llm_call_slot", gate),
            patch(
                "core.services.llm.structured._native_with_retry",
                AsyncMock(return_value=result),
            ),
        ):
            await generate_structured(service, "q", model=MODEL)
        assert gate.await_count == 1

        with (
            patch("core.services.llm.message_runtime.acquire_llm_call_slot", gate),
            patch(
                "core.services.llm.message_runtime._messages_with_retry",
                AsyncMock(return_value=result),
            ),
        ):
            await generate_messages(service, [Message.user("q")], model=MODEL)
        assert gate.await_count == 2

        image = GeneratedImage(data=b"x", media_type="image/png", model="m")
        painter = SimpleNamespace(
            provider=SimpleNamespace(generate_image=AsyncMock(return_value=image)),
            config=SimpleNamespace(provider="openai"),
        )
        with patch("core.services.llm.images.acquire_llm_call_slot", gate):
            await generate_image(painter, "a lighthouse")
        gate.assert_awaited_with("openai")
        assert gate.await_count == 3


class TestScope:
    async def test_each_provider_has_its_own_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=1)

        await acquire_llm_call_slot("openai")
        await acquire_llm_call_slot("anthropic")  # separate window
        with pytest.raises(LocalLLMRateLimitError):
            await acquire_llm_call_slot("OpenAI")

    async def test_a_shared_window_spans_providers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _configure(monkeypatch, llm_rate_limit=1, llm_rate_per_provider=False)

        await acquire_llm_call_slot("openai")
        with pytest.raises(LocalLLMRateLimitError) as info:
            await acquire_llm_call_slot("anthropic")
        assert info.value.key == "llm:*"

    def test_keys(self) -> None:
        assert llm_rate_limit_key("OpenAI") == "llm:openai"
        assert llm_rate_limit_key(None) == "llm:*"
        assert llm_rate_limit_key("openai", per_provider=False) == "llm:*"
