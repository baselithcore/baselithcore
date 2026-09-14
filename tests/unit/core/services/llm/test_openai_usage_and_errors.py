"""OpenAI: token cap, usage extraction and typed error mapping.

The Anthropic half of the same contract lives in
``test_provider_usage_and_errors``; both providers are driven through fake SDK
clients, never the network.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.llm.errors import (
    LLMClientError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
)
from core.services.llm.usage import Usage


# Stand-ins for the SDK's exception shape (both SDKs declare it identically):
# APIStatusError carries ``status_code``, APITimeoutError sits under
# APIConnectionError.
class APIError(Exception):
    pass


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIConnectionError):
    pass


class APIStatusError(APIError):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code
        self.response = SimpleNamespace(headers={})


def _completion(content="ok", finish_reason="stop", usage=None, refusal=None):
    message = SimpleNamespace(
        content=content, tool_calls=None, refusal=refusal, role="assistant"
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage
        or SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        ),
    )


def _openai(create=None):
    client = MagicMock()
    client.chat.completions.create = create or AsyncMock(return_value=_completion())
    with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
        mock_openai.AsyncOpenAI.return_value = client
        from core.services.llm.providers.openai_provider import OpenAIProvider

        provider = OpenAIProvider(api_key="sk-test")
        provider._ensure_client()
        return provider, client


@pytest.mark.asyncio
class TestOpenAITokenCap:
    async def test_generate_sends_max_completion_tokens(self):
        provider, client = _openai()
        await provider.generate("hi", model="gpt-5", max_tokens=256)
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 256
        assert "max_tokens" not in kwargs

    async def test_structured_sends_max_completion_tokens(self):
        provider, client = _openai()
        await provider.generate_structured("hi", model="gpt-5", max_tokens=256)
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 256
        assert "max_tokens" not in kwargs

    async def test_stream_sends_max_completion_tokens(self):
        async def _empty(**kwargs):
            if False:  # pragma: no cover - never yields
                yield None

        client = MagicMock()
        client.chat.completions.create = AsyncMock(return_value=_empty())
        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            async for _ in provider.generate_stream("hi", "gpt-5", max_tokens=64):
                pass
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 64
        assert "max_tokens" not in kwargs

    async def test_cross_provider_kwargs_never_reach_the_api(self):
        provider, client = _openai()
        await provider.generate(
            "hi", model="gpt-5", effort="high", allow_refusal=True, usage_sink=[]
        )
        kwargs = client.chat.completions.create.call_args.kwargs
        for reserved in ("effort", "allow_refusal", "usage_sink"):
            assert reserved not in kwargs


@pytest.mark.asyncio
class TestOpenAIUsage:
    async def test_cached_prompt_tokens_are_split_out(self):
        provider, _ = _openai()
        result = await provider.generate_structured("hi", model="gpt-5")
        assert result.usage == Usage(
            input_tokens=20, output_tokens=20, cache_read_tokens=80
        )
        assert result.tokens_used == 120

    async def test_generate_publishes_usage_to_a_sink(self):
        provider, _ = _openai()
        sink: list[Usage] = []
        _, tokens = await provider.generate("hi", model="gpt-5", usage_sink=sink)
        assert tokens == 120
        assert sink[-1].cache_read_tokens == 80

    async def test_refusal_message_raises(self):
        provider, _ = _openai(
            create=AsyncMock(
                return_value=_completion(content=None, refusal="I won't do that")
            )
        )
        with pytest.raises(LLMRefusalError):
            await provider.generate("hi", model="gpt-5")

    async def test_length_finish_reason_is_reported_as_truncation(self):
        provider, _ = _openai(
            create=AsyncMock(return_value=_completion(finish_reason="length"))
        )
        result = await provider.generate_structured("hi", model="gpt-5")
        assert result.stop_reason == "length"


@pytest.mark.asyncio
class TestOpenAIErrorMapping:
    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (APIStatusError("throttled", 429), LLMRateLimitError),
            (APIStatusError("boom", 500), LLMServerError),
            (APIStatusError("bad key", 401), LLMClientError),
            (APITimeoutError("slow"), LLMTimeoutError),
        ],
    )
    async def test_sdk_exceptions_map_to_the_taxonomy(self, raised, expected):
        provider, _ = _openai(create=AsyncMock(side_effect=raised))
        with pytest.raises(expected):
            await provider.generate("hi", model="gpt-5")


@pytest.mark.asyncio
class TestOpenAIStreamErrorMapping:
    async def test_a_throttled_stream_maps_to_the_neutral_class(self):
        client = MagicMock()
        client.chat.completions.create = AsyncMock(
            side_effect=APIStatusError("slow down", 429)
        )
        with patch("core.services.llm.providers.openai_provider.openai") as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="sk-test")
            with pytest.raises(LLMRateLimitError):
                async for _ in provider.generate_stream("hi", "gpt-5"):
                    pass


@pytest.mark.asyncio
class TestOpenAIRefusalLogging:
    async def test_a_refusal_is_not_logged_as_a_generation_error(self):
        """Symmetric with Anthropic: a refusal is a model decision."""
        from core.services.llm.providers import openai_provider as module

        provider, _ = _openai(
            create=AsyncMock(
                return_value=_completion(content=None, refusal="I won't do that")
            )
        )
        with patch.object(module.logger, "error") as error_log:
            with pytest.raises(LLMRefusalError):
                await provider.generate("hi", model="gpt-5")
        error_log.assert_not_called()

    async def test_a_real_failure_is_still_logged_as_an_error(self):
        from core.services.llm.providers import openai_provider as module

        provider, _ = _openai(create=AsyncMock(side_effect=APIStatusError("x", 500)))
        with patch.object(module.logger, "error") as error_log:
            with pytest.raises(LLMServerError):
                await provider.generate("hi", model="gpt-5")
        assert error_log.called
