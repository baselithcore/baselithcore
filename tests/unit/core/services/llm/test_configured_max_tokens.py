"""``LLM_MAX_TOKENS`` reaches the provider on every generation path.

The setting was bound on ``LLMConfig`` and advertised in ``.env.example``, but
no call path read it: setting it capped nothing, and the providers without a
per-family default (OpenAI, Gemini, Ollama) generated up to the model's own
ceiling on every call. An explicit per-call ``max_tokens`` still wins.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from core.services.llm.message_runtime import generate_messages
from core.services.llm.messages import Message
from core.services.llm.model_capabilities import configured_max_tokens
from core.services.llm.service import LLMService
from core.services.llm.structured import generate_structured
from core.services.llm.tool_calling import LLMResult

pytestmark = [pytest.mark.unit]

MODEL = "gpt-4o-mini"


@pytest.fixture(autouse=True)
def _isolated_token_ledger():
    """Keep token reports out of the process-wide cost context (see
    ``test_cost_gate_on_every_path`` for why)."""
    from core.middleware.cost_control import _cost_context

    token = _cost_context.set(None)
    try:
        yield
    finally:
        _cost_context.reset(token)


def _service(max_tokens: object, *, messages: bool = False) -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as config:
        config.return_value = Mock(
            provider="openai",
            model=MODEL,
            enable_cache=False,
            fallback_chain="",
            max_concurrent_requests=0,
            enable_native_tools=messages,
            thinking_enabled=False,
            max_tokens=max_tokens,
        )
        service = LLMService()
    service.provider = SimpleNamespace(
        supports_native_tools=messages, supports_messages=messages
    )
    service.cost_tracker = None
    return service


class TestConfiguredMaxTokens:
    def test_explicit_wins(self):
        assert configured_max_tokens(64, SimpleNamespace(max_tokens=4096)) == 64

    def test_config_fills_the_gap(self):
        assert configured_max_tokens(None, SimpleNamespace(max_tokens=4096)) == 4096

    @pytest.mark.parametrize("value", [None, 0, -1, True, "4096", Mock()])
    def test_unset_or_invalid_config_keeps_the_provider_default(self, value):
        assert configured_max_tokens(None, SimpleNamespace(max_tokens=value)) is None


class TestEveryPathForwardsIt:
    async def test_text_path(self):
        service = _service(777)
        service._generate_with_retry = AsyncMock(return_value=("hi", 10))
        await service.generate_response("q", model=MODEL)
        assert service._generate_with_retry.await_args.kwargs["max_tokens"] == 777

    async def test_text_path_explicit_override(self):
        service = _service(777)
        service._generate_with_retry = AsyncMock(return_value=("hi", 10))
        await service.generate_response("q", model=MODEL, max_tokens=32)
        assert service._generate_with_retry.await_args.kwargs["max_tokens"] == 32

    async def test_text_path_without_config_sends_nothing(self):
        service = _service(None)
        service._generate_with_retry = AsyncMock(return_value=("hi", 10))
        await service.generate_response("q", model=MODEL)
        assert "max_tokens" not in service._generate_with_retry.await_args.kwargs

    async def test_coercion_structured_path(self):
        service = _service(555)
        service._generate_with_retry = AsyncMock(return_value=("plain", 10))
        await generate_structured(service, "q", model=MODEL)
        assert service._generate_with_retry.await_args.kwargs["max_tokens"] == 555

    async def test_message_path(self):
        service = _service(333, messages=True)
        turn = AsyncMock(return_value=LLMResult(text="hi", tokens_used=10))
        with patch("core.services.llm.message_runtime._messages_with_retry", turn):
            await generate_messages(service, [Message.user("q")], model=MODEL)
        assert turn.await_args.kwargs["max_tokens"] == 333
