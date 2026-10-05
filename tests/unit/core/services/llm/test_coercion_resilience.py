"""Coerced tool turns: failover, thinking-free JSON mode, fail-closed parsing.

A tool turn on a provider without a native tool API (a vLLM server started
without a tool parser) goes through the prompt-coercion path: tools described
in the system prompt, a JSON object parsed back into a tool call. That path has
to keep the guarantees the native one gives — cross-provider failover through
``LLM_FALLBACK_CHAIN`` — and survive what a thinking model actually returns:
reasoning wrapped around the JSON or a malformed object (prose around it is
rejected and re-asked; see ``test_coercion_fail_closed``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from core.resilience.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_circuit_breaker,
)
from core.services.llm import fallback_runtime
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.messages import Message, ToolResultBlock, ToolUseBlock
from core.services.llm.service import LLMService
from core.services.llm.structured import _parse_fallback
from core.services.llm.tool_calling import LLMResult, LLMToolSpec

_SDK = "core.services.llm.providers.openai_provider.openai"

_POP_SPEC = LLMToolSpec(
    name="pop",
    description="Look up a city's population.",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)

_CALL = '{"tool": "pop", "arguments": {"city": "Rome"}}'


@pytest.fixture(autouse=True)
def _fresh_breakers():
    def _close() -> None:
        for name in ("vllm_provider", "openai_provider", "ollama_provider"):
            breaker = get_circuit_breaker(name)
            breaker._state = CircuitState.CLOSED
            breaker._stats = CircuitStats()
            breaker._half_open_attempts = 0

    _close()
    fallback_runtime.reset_fallback_services()
    yield
    _close()
    fallback_runtime.reset_fallback_services()


@pytest.fixture
def client():
    """The OpenAI SDK client the vLLM provider builds."""
    fake = AsyncMock()
    with patch(_SDK) as mock_openai:
        mock_openai.AsyncOpenAI.return_value = fake
        yield fake


def _completion(text: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    response.choices[0].message.refusal = None
    response.choices[0].message.tool_calls = None
    response.choices[0].finish_reason = "stop"
    response.usage.total_tokens = 7
    response.usage.prompt_tokens = 5
    response.usage.completion_tokens = 2
    response.usage.prompt_tokens_details = None
    response.usage.completion_tokens_details = None
    return response


def _service(*, chain: str = "", primary: str = "vllm") -> LLMService:
    with patch("core.services.llm.service.get_llm_config") as mock_config:
        mock_config.return_value = Mock(
            provider="ollama",
            model="qwen",
            enable_cache=False,
            enable_native_tools=True,
            fallback_chain=chain,
            max_concurrent_requests=0,
        )
        service = LLMService()
    service.config.provider = primary
    return service


def _vllm_service(client: AsyncMock) -> LLMService:
    """A real ``LLMService`` on a real parserless ``VLLMProvider``."""
    from core.services.llm.providers.vllm_provider import VLLMProvider

    service = _service()
    service.provider = VLLMProvider(api_base="http://gpu:8000", native_tools=False)
    return service


def _sent(client: AsyncMock) -> list[dict]:
    return [call.kwargs for call in client.chat.completions.create.await_args_list]


@pytest.mark.asyncio
class TestCoercedTurnFailsOver:
    async def test_a_failing_primary_falls_through_to_a_chain_stage(self, monkeypatch):
        service = _service(chain="openai:gpt-x")
        service.provider = Mock(supports_native_tools=False, supports_messages=True)
        service.provider.generate = AsyncMock(
            side_effect=LLMProviderError("primary down")
        )
        clone = Mock()
        clone._generate_with_retry = AsyncMock(return_value=(_CALL, 9))
        monkeypatch.setattr(fallback_runtime, "_clone_service", lambda *a, **k: clone)

        result = await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        service.provider.generate.assert_awaited()
        clone._generate_with_retry.assert_awaited_once()
        sent = clone._generate_with_retry.await_args.kwargs
        assert sent["json_mode"] is True
        assert '"pop"' in sent["system"]
        assert [c.name for c in result.tool_calls] == ["pop"]
        assert result.tool_calls[0].arguments == {"city": "Rome"}


@pytest.mark.asyncio
class TestVLLMJsonModeDisablesThinking:
    async def test_json_mode_turns_thinking_off(self, client):
        from core.services.llm.providers.vllm_provider import VLLMProvider

        client.chat.completions.create.return_value = _completion("{}")
        provider = VLLMProvider(api_base="http://gpu:8000")

        await provider.generate("p", "qwen", json_mode=True)

        request = _sent(client)[0]
        assert request["response_format"] == {"type": "json_object"}
        assert request["extra_body"] == {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    async def test_existing_extra_body_is_merged_not_replaced(self, client):
        from core.services.llm.providers.vllm_provider import VLLMProvider

        client.chat.completions.create.return_value = _completion("{}")
        provider = VLLMProvider(api_base="http://gpu:8000")
        extra = {"top_k": 20, "chat_template_kwargs": {"custom": 1}}

        await provider.generate("p", "qwen", json_mode=True, extra_body=extra)

        assert _sent(client)[0]["extra_body"] == {
            "top_k": 20,
            "chat_template_kwargs": {"custom": 1, "enable_thinking": False},
        }
        # The caller's dict is not mutated.
        assert extra == {"top_k": 20, "chat_template_kwargs": {"custom": 1}}

    async def test_an_explicit_caller_choice_wins(self, client):
        from core.services.llm.providers.vllm_provider import VLLMProvider

        client.chat.completions.create.return_value = _completion("{}")
        provider = VLLMProvider(api_base="http://gpu:8000")
        extra = {"chat_template_kwargs": {"enable_thinking": True}}

        await provider.generate("p", "qwen", json_mode=True, extra_body=extra)

        assert _sent(client)[0]["extra_body"] == extra

    async def test_plain_text_leaves_thinking_alone(self, client):
        from core.services.llm.providers.vllm_provider import VLLMProvider

        client.chat.completions.create.return_value = _completion("hi")
        provider = VLLMProvider(api_base="http://gpu:8000")

        await provider.generate("p", "qwen")

        assert "extra_body" not in _sent(client)[0]


class TestTolerantParsing:
    def test_think_wrapped_json_parses(self):
        result = _parse_fallback(f"<think>I should call pop.</think>\n{_CALL}", True)
        assert [c.name for c in result.tool_calls] == ["pop"]
        assert result.tool_calls[0].arguments == {"city": "Rome"}

    def test_an_unterminated_leading_think_tag_is_dropped(self):
        result = _parse_fallback(f"<think>{_CALL}", True)
        assert [c.name for c in result.tool_calls] == ["pop"]

    def test_json_inside_prose_is_not_extracted(self):
        # Fail-closed (see test_coercion_fail_closed): prose + JSON is the raw
        # text, never a parsed object, let alone a tool call.
        reply = (
            'Sure, here it is: {"tool": null, "final": "Rome: {2.87M}"} hope it helps'
        )
        result = _parse_fallback(reply, True)
        assert result.tool_calls == []
        assert result.text == reply

    def test_a_tool_call_after_a_braced_aside_is_not_executed(self):
        reply = f"Plan {{call pop}} -> {_CALL}"
        result = _parse_fallback(reply, True)
        assert result.tool_calls == []
        assert result.text == reply

    def test_garbage_still_degrades_to_text(self):
        assert _parse_fallback('{"tool": "null, "', True).text == '{"tool": "null, "'


@pytest.mark.asyncio
class TestOneRecoverableReAsk:
    async def test_garbage_triggers_exactly_one_re_ask(self, client):
        client.chat.completions.create.side_effect = [
            _completion('{"tool_use": "pop", "arguments": {"city": "Rome"}}'),
            _completion(_CALL),
        ]
        service = _vllm_service(client)

        result = await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        requests = _sent(client)
        assert len(requests) == 2
        assert (
            "Reply again with ONLY a valid JSON object"
            in (requests[1]["messages"][-1]["content"])
        )
        assert [c.name for c in result.tool_calls] == ["pop"]
        # Both calls are billed to the turn.
        assert result.tokens_used == 14

    async def test_a_second_failure_returns_the_original_text(self, client):
        client.chat.completions.create.side_effect = [
            _completion('{"tool": "null, "'),
            _completion("still not json"),
        ]
        service = _vllm_service(client)

        result = await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        assert len(_sent(client)) == 2
        assert result.tool_calls == []
        assert result.text == '{"tool": "null, "'

    async def test_a_valid_reply_is_not_re_asked(self, client):
        client.chat.completions.create.return_value = _completion(_CALL)
        service = _vllm_service(client)

        await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        assert len(_sent(client)) == 1


def _stage(*, native_tools: bool, text: str) -> Mock:
    provider = Mock(supports_native_tools=native_tools, supports_messages=True)
    provider.generate_messages = AsyncMock(return_value=LLMResult(text=text))
    return provider


@pytest.mark.asyncio
class TestMessageChainStageSkip:
    def _primary_down(self, chain: str) -> LLMService:
        service = _service(chain=chain, primary="anthropic")
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        service.provider.generate_messages = AsyncMock(
            side_effect=LLMProviderError("primary down")
        )
        return service

    async def test_a_native_stage_after_a_skipped_parserless_one_serves(
        self, monkeypatch
    ):
        service = self._primary_down("vllm:qwen,openai:gpt-x")
        parserless = _stage(native_tools=False, text="400")
        native = _stage(native_tools=True, text="served")
        stages = {"vllm": parserless, "openai": native}
        monkeypatch.setattr(
            fallback_runtime,
            "_clone_service",
            lambda _svc, provider, _model: Mock(provider=stages[provider]),
        )

        result = await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        assert result.text == "served"
        parserless.generate_messages.assert_not_awaited()

    async def test_tool_history_alone_does_not_skip_a_parserless_stage(
        self, monkeypatch
    ):
        """No tools offered: nothing a parserless server rejects is sent."""
        service = self._primary_down("vllm:qwen")
        parserless = _stage(native_tools=False, text="Rome has 2.87M people.")
        monkeypatch.setattr(
            fallback_runtime,
            "_clone_service",
            lambda *_a, **_k: Mock(provider=parserless),
        )
        history = [
            Message.user("population of Rome?"),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="t1", name="pop", input={"city": "Rome"})],
            ),
            Message.tool_results([ToolResultBlock(tool_use_id="t1", content="2.87M")]),
        ]

        result = await service.generate_messages(history)

        assert result.text == "Rome has 2.87M people."
        parserless.generate_messages.assert_awaited_once()
        assert parserless.generate_messages.await_args.kwargs.get("tools") is None
