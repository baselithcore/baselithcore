"""A vLLM server without tool parsers must never see native tool parameters.

``LLM_VLLM_NATIVE_TOOLS=false`` says the server was started without
``--enable-auto-tool-choice`` / ``--tool-call-parser``. Such a server answers a
request carrying ``tool_choice="auto"`` with HTTP 400, so the message path —
the one every ``core.agent.Agent`` turn takes — has to fall back to the prompt
coercion path (tools described in the system prompt, a JSON object parsed back
into a tool call) exactly as the structured path already did.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from core.agent import Agent
from core.resilience.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_circuit_breaker,
)
from core.services.llm.messages import Message
from core.services.llm.service import LLMService
from core.services.llm.tool_calling import LLMToolSpec

_SDK = "core.services.llm.providers.openai_provider.openai"

_POP_SPEC = LLMToolSpec(
    name="pop",
    description="Look up a city's population.",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}},
)


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


@pytest.fixture
def client():
    """The OpenAI SDK client the provider builds (lazily, per endpoint)."""
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


def _vllm_service(client: AsyncMock, *, native_tools: bool) -> LLMService:
    """A real ``LLMService`` driving a real ``VLLMProvider`` over a fake SDK."""
    with patch("core.services.llm.service.get_llm_config") as mock_config:
        mock_config.return_value = Mock(
            provider="ollama",
            model="qwen",
            enable_cache=False,
            enable_native_tools=True,
            fallback_chain="",
            max_concurrent_requests=0,
        )
        service = LLMService()
    service.config.provider = "vllm"
    from core.services.llm.providers.vllm_provider import VLLMProvider

    service.provider = VLLMProvider(
        api_base="http://gpu:8000", native_tools=native_tools
    )
    return service


def _sent(client: AsyncMock) -> list[dict]:
    return [call.kwargs for call in client.chat.completions.create.await_args_list]


@pytest.mark.asyncio
class TestMessagePath:
    async def test_no_native_tool_params_reach_a_parserless_server(self, client):
        client.chat.completions.create.return_value = _completion(
            '{"tool": "pop", "arguments": {"city": "Rome"}}'
        )
        service = _vllm_service(client, native_tools=False)

        result = await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        for request in _sent(client):
            assert "tools" not in request
            assert "tool_choice" not in request
        # The tools are still offered — in the system prompt.
        system = _sent(client)[0]["messages"][0]
        assert system["role"] == "system"
        assert '"pop"' in system["content"]
        assert [c.name for c in result.tool_calls] == ["pop"]
        assert result.tool_calls[0].arguments == {"city": "Rome"}

    async def test_a_tool_free_turn_keeps_the_message_api(self, client):
        """No tools requested: nothing a parserless server could reject."""
        client.chat.completions.create.return_value = _completion("hello")
        service = _vllm_service(client, native_tools=False)

        result = await service.generate_messages([Message.user("hi")])

        assert result.text == "hello"
        assert result.native is True
        assert "tools" not in _sent(client)[0]

    async def test_native_tools_still_go_native(self, client):
        client.chat.completions.create.return_value = _completion("2.87 million")
        service = _vllm_service(client, native_tools=True)

        await service.generate_messages(
            [Message.user("population of Rome?")], tools=[_POP_SPEC]
        )

        request = _sent(client)[0]
        assert request["tools"][0]["function"]["name"] == "pop"
        assert request["tool_choice"] == "auto"


async def pop(city: str) -> str:
    """Look up a city's population."""
    return f"{city}:2870000"


@pytest.mark.asyncio
class TestAgentOnAParserlessServer:
    async def test_an_agent_completes_a_tool_call_through_the_text_fallback(
        self, client
    ):
        client.chat.completions.create.side_effect = [
            _completion('{"tool": "pop", "arguments": {"city": "Rome"}}'),
            _completion('{"tool": null, "final": "Rome has 2.87 million people."}'),
        ]
        service = _vllm_service(client, native_tools=False)

        result = await Agent(tools=[pop], llm_service=service).run(
            "population of Rome?"
        )

        assert result.output == "Rome has 2.87 million people."
        requests = _sent(client)
        assert len(requests) == 2
        for request in requests:
            assert "tools" not in request
            assert "tool_choice" not in request
        # The tool really ran and its observation went back to the model.
        follow_up = json.dumps(requests[1]["messages"])
        assert "Rome:2870000" in follow_up


@pytest.mark.asyncio
class TestFallbackChain:
    async def test_a_parserless_stage_is_skipped_on_a_tool_turn(self, monkeypatch):
        """A chain stage must not 400 where the primary would have degraded."""
        from core.services.llm import fallback_runtime
        from core.services.llm.exceptions import LLMProviderError
        from core.services.llm.tool_calling import LLMResult

        fallback_runtime.reset_fallback_services()
        with patch("core.services.llm.service.get_llm_config") as mock_config:
            mock_config.return_value = Mock(
                provider="ollama",
                model="claude-opus-5",
                enable_cache=False,
                enable_native_tools=True,
                fallback_chain="vllm:qwen",
                max_concurrent_requests=0,
            )
            service = LLMService()
        service.provider = Mock(supports_native_tools=True, supports_messages=True)
        service.provider.generate_messages = AsyncMock(
            side_effect=LLMProviderError("primary down")
        )
        stage = Mock(supports_native_tools=False, supports_messages=True)
        stage.generate_messages = AsyncMock(return_value=LLMResult(text="400"))
        clone = Mock(provider=stage, config=service.config)
        monkeypatch.setattr(fallback_runtime, "_clone_service", lambda *a, **k: clone)

        with pytest.raises(LLMProviderError):
            await service.generate_messages(
                [Message.user("population of Rome?")], tools=[_POP_SPEC]
            )
        stage.generate_messages.assert_not_awaited()
