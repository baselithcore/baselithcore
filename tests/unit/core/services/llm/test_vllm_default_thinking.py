"""``LLM_VLLM_ENABLE_THINKING`` sets the chat template's thinking on every vLLM request."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from baselith.core.config.services import LLMConfig
from baselith.core.resilience.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_circuit_breaker,
)
from baselith.core.services.llm.providers.vllm_provider import (
    VLLMProvider,
    _with_default_thinking,
)

_SDK = "baselith.core.services.llm.providers.openai_provider.openai"
_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.fixture(autouse=True)
def _fresh_breakers():
    def _close() -> None:
        for name in ("vllm_provider", "openai_provider"):
            breaker = get_circuit_breaker(name)
            breaker._state = CircuitState.CLOSED
            breaker._stats = CircuitStats()
            breaker._half_open_attempts = 0

    _close()
    yield
    _close()


def _completion(text: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    response.choices[0].message.refusal = None
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = "stop"
    response.usage.total_tokens = 7
    return response


class TestHelper:
    def test_unset_sends_nothing(self):
        kwargs = {"temperature": 0.1}
        assert _with_default_thinking(kwargs, None) is kwargs

    def test_false_and_true_are_sent(self):
        assert _with_default_thinking({}, False)["extra_body"] == _OFF
        on = _with_default_thinking({}, True)["extra_body"]
        assert on == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_other_extra_body_is_merged_and_not_mutated(self):
        extra = {"top_k": 20, "chat_template_kwargs": {"custom": 1}}
        out = _with_default_thinking({"extra_body": extra}, False)
        assert out["extra_body"] == {
            "top_k": 20,
            "chat_template_kwargs": {"custom": 1, "enable_thinking": False},
        }
        assert extra == {"top_k": 20, "chat_template_kwargs": {"custom": 1}}

    def test_a_callers_own_choice_wins(self):
        mine = {"chat_template_kwargs": {"enable_thinking": True}}
        assert _with_default_thinking({"extra_body": mine}, False)["extra_body"] == mine


@pytest.mark.asyncio
class TestProviderRequests:
    async def _provider(self, enable_thinking):
        client = AsyncMock()
        client.chat.completions.create.return_value = _completion()
        return client, VLLMProvider(
            api_base="http://gpu:8000", enable_thinking=enable_thinking
        )

    async def test_plain_generate_sends_thinking_off(self):
        client, provider = await self._provider(False)
        with patch(_SDK) as sdk:
            sdk.AsyncOpenAI.return_value = client
            await provider.generate("p", "qwen")
        assert client.chat.completions.create.await_args.kwargs["extra_body"] == _OFF

    async def test_json_mode_still_turns_thinking_off_when_the_default_is_on(self):
        client, provider = await self._provider(True)
        with patch(_SDK) as sdk:
            sdk.AsyncOpenAI.return_value = client
            await provider.generate("p", "qwen", json_mode=True)
        assert client.chat.completions.create.await_args.kwargs["extra_body"] == _OFF

    async def test_unset_changes_nothing(self):
        client, provider = await self._provider(None)
        with patch(_SDK) as sdk:
            sdk.AsyncOpenAI.return_value = client
            await provider.generate("p", "qwen")
        assert "extra_body" not in client.chat.completions.create.await_args.kwargs

    async def test_messages_and_structured_paths_carry_it_too(self):
        from baselith.core.services.llm.messages import Message

        for call in ("generate_messages", "generate_structured"):
            client, provider = await self._provider(False)
            with patch(_SDK) as sdk:
                sdk.AsyncOpenAI.return_value = client
                if call == "generate_messages":
                    await provider.generate_messages([Message.user("hi")], "qwen")
                else:
                    await provider.generate_structured("hi", "qwen")
            sent = client.chat.completions.create.await_args.kwargs
            assert sent["extra_body"] == _OFF, call

    async def test_stream_paths_carry_it_too(self):
        from baselith.core.services.llm.messages import Message

        async def _chunks():
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = "x"
            chunk.choices[0].delta.reasoning_content = None
            chunk.usage = None
            yield chunk

        client, provider = await self._provider(False)
        client.chat.completions.create.return_value = _chunks()
        with patch(_SDK) as sdk:
            sdk.AsyncOpenAI.return_value = client
            async for _ in provider.generate_stream("hi", "qwen"):
                pass
        assert client.chat.completions.create.await_args.kwargs["extra_body"] == _OFF

        client, provider = await self._provider(False)
        client.chat.completions.create.return_value = _chunks()
        with patch(_SDK) as sdk:
            sdk.AsyncOpenAI.return_value = client
            async for _ in provider.generate_messages_stream(
                [Message.user("hi")], "qwen"
            ):
                pass
        assert client.chat.completions.create.await_args.kwargs["extra_body"] == _OFF


class TestConfigAndFactory:
    def test_environment_variable(self, monkeypatch):
        monkeypatch.setenv("LLM_VLLM_ENABLE_THINKING", "false")
        assert LLMConfig().vllm_enable_thinking is False
        monkeypatch.setenv("LLM_VLLM_ENABLE_THINKING", "")
        assert LLMConfig().vllm_enable_thinking is None
        monkeypatch.delenv("LLM_VLLM_ENABLE_THINKING")
        assert LLMConfig().vllm_enable_thinking is None

    def test_factory_forwards_the_setting(self):
        from baselith.core.services.llm.provider_factory import create_provider

        config = SimpleNamespace(
            provider="vllm",
            vllm_api_base="http://gpu:8000",
            vllm_endpoints=None,
            vllm_api_key=None,
            vllm_native_tools=True,
            vllm_enable_thinking=False,
            api_base=None,
            api_key=None,
            request_timeout=30.0,
            connect_timeout=5.0,
        )
        provider = create_provider(config)
        assert provider._enable_thinking is False
