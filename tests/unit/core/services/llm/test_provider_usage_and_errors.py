"""Anthropic: usage extraction, stop-reason branches, typed error mapping.

The exact-usage split, the refusal/truncation policy, ``pause_turn``
continuation and the neutral exception taxonomy, driven through a fake SDK
client. The OpenAI half lives in ``test_openai_usage_and_errors``.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.llm.errors import (
    LLMClientError,
    LLMConnectionError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMServerError,
    LLMTimeoutError,
)
from core.services.llm.usage import Usage

MODERN = "claude-opus-5"


# --------------------------------------------------------------------------
# Fake SDK exceptions (same shape in both SDKs)
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# Anthropic
# --------------------------------------------------------------------------
def _message(text="ok", stop_reason="end_turn", usage=None, stop_details=None):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(
        content=[block],
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=usage
        or SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=50,
        ),
    )


def _anthropic(create=None, stream=None):
    client = MagicMock()
    client.messages.create = create or AsyncMock(return_value=_message())
    if stream is not None:
        client.messages.stream = stream
    with patch(
        "core.services.llm.providers.anthropic_provider.anthropic"
    ) as mock_anthropic:
        mock_anthropic.AsyncAnthropic.return_value = client
        mock_anthropic.NOT_GIVEN = "not_given"
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test")
        provider._ensure_client()
        return provider, client


@pytest.mark.asyncio
class TestAnthropicUsage:
    async def test_structured_result_carries_the_four_buckets(self):
        provider, _ = _anthropic()
        result = await provider.generate_structured("hi", model=MODERN)
        assert result.usage == Usage(
            input_tokens=100,
            output_tokens=20,
            cache_write_tokens=5,
            cache_read_tokens=50,
        )
        # The legacy integer keeps working, as the sum of every bucket.
        assert result.tokens_used == 175

    async def test_generate_publishes_usage_to_a_sink(self):
        provider, _ = _anthropic()
        sink: list[Usage] = []
        _, tokens = await provider.generate("hi", model=MODERN, usage_sink=sink)
        assert tokens == 175
        assert sink[-1].input_tokens == 100
        assert sink[-1].output_tokens == 20
        assert sink[-1].estimated is False

    async def test_missing_usage_falls_back_to_a_flagged_estimate(self):
        provider, _ = _anthropic(
            create=AsyncMock(return_value=_message(usage=SimpleNamespace()))
        )
        sink: list[Usage] = []
        await provider.generate("hello there", model=MODERN, usage_sink=sink)
        assert sink[-1].estimated is True
        assert sink[-1].total > 0

    async def test_text_stream_reads_message_start_and_message_delta(self):
        events = [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=1000,
                        output_tokens=0,
                        cache_creation_input_tokens=0,
                        cache_read_input_tokens=0,
                    )
                ),
            ),
            SimpleNamespace(type="text_delta", text="hello"),
            SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(
                    input_tokens=0,
                    output_tokens=77,
                    cache_creation_input_tokens=0,
                    cache_read_input_tokens=0,
                ),
            ),
        ]

        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                async def gen():
                    for event in events:
                        yield event

                return gen()

        provider, _ = _anthropic(stream=lambda **kwargs: _Stream())
        chunks = [c async for c in provider.generate_stream("hi", model=MODERN)]
        # Text first, counted from the exact prompt side reported by
        # message_start (1000) rather than the two-token local estimate...
        assert chunks[0][0] == "hello"
        assert chunks[0][1] > 1000
        # ...then the billed total as an empty terminal chunk.
        assert chunks[-1] == ("", 1077)


@pytest.mark.asyncio
class TestAnthropicStopReasons:
    async def test_refusal_raises(self):
        provider, _ = _anthropic(
            create=AsyncMock(
                return_value=_message(
                    text="",
                    stop_reason="refusal",
                    stop_details=SimpleNamespace(
                        category="safety", explanation="declined"
                    ),
                )
            )
        )
        with pytest.raises(LLMRefusalError) as exc_info:
            await provider.generate("hi", model=MODERN)
        assert exc_info.value.category == "safety"

    async def test_allow_refusal_returns_the_text_instead(self):
        provider, _ = _anthropic(
            create=AsyncMock(
                return_value=_message(text="I can't", stop_reason="refusal")
            )
        )
        content, _ = await provider.generate("hi", model=MODERN, allow_refusal=True)
        assert content == "I can't"

    async def test_structured_surfaces_stop_reason_and_details(self):
        provider, _ = _anthropic(
            create=AsyncMock(
                return_value=_message(
                    stop_reason="refusal",
                    stop_details=SimpleNamespace(category="safety", explanation="no"),
                )
            )
        )
        result = await provider.generate_structured("hi", model=MODERN)
        assert result.stop_reason == "refusal"
        assert result.stop_details == {"category": "safety", "explanation": "no"}

    async def test_pause_turn_is_resumed_and_content_is_merged(self):
        paused = _message(text="part one ", stop_reason="pause_turn")
        done = _message(text="part two", stop_reason="end_turn")
        create = AsyncMock(side_effect=[paused, done])
        provider, _ = _anthropic(create=create)

        content, tokens = await provider.generate("hi", model=MODERN)

        assert content == "part one part two"
        assert create.await_count == 2
        # Both turns are billed, so both are counted.
        assert tokens == 350
        # The second request carries the first turn's assistant content.
        resumed = create.await_args_list[1].kwargs["messages"]
        assert resumed[-1]["role"] == "assistant"

    async def test_pause_turn_gives_up_after_three_continuations(self):
        create = AsyncMock(return_value=_message(text="x", stop_reason="pause_turn"))
        provider, _ = _anthropic(create=create)
        content, _ = await provider.generate("hi", model=MODERN)
        assert create.await_count == 4  # initial call + 3 continuations
        assert content == "xxxx"


@pytest.mark.asyncio
class TestAnthropicErrorMapping:
    @pytest.mark.parametrize(
        ("raised", "expected"),
        [
            (APIStatusError("throttled", 429), LLMRateLimitError),
            (APIStatusError("boom", 503), LLMServerError),
            (APIStatusError("bad model", 404), LLMClientError),
            (APIConnectionError("dns"), LLMConnectionError),
            (APITimeoutError("slow"), LLMTimeoutError),
        ],
    )
    async def test_sdk_exceptions_map_to_the_taxonomy(self, raised, expected):
        provider, _ = _anthropic(create=AsyncMock(side_effect=raised))
        with pytest.raises(expected):
            await provider.generate("hi", model=MODERN)

    async def test_structured_path_maps_too(self):
        provider, _ = _anthropic(
            create=AsyncMock(side_effect=APIStatusError("throttled", 429))
        )
        with pytest.raises(LLMRateLimitError):
            await provider.generate_structured("hi", model=MODERN)


@pytest.mark.asyncio
class TestPauseTurnResilience:
    async def test_a_failed_continuation_keeps_the_partial_answer(self):
        """A resumed turn that dies must not throw away the billed first turn."""
        paused = _message(text="part one ", stop_reason="pause_turn")
        create = AsyncMock(side_effect=[paused, APIStatusError("boom", 503)])
        provider, _ = _anthropic(create=create)

        content, tokens = await provider.generate("hi", model=MODERN)

        assert content == "part one"
        assert tokens == 175  # the first turn was billed and is still counted
        assert create.await_count == 2

    async def test_a_failure_on_the_first_call_still_raises(self):
        # Nothing was accumulated, so there is nothing to return instead.
        create = AsyncMock(side_effect=APIStatusError("boom", 503))
        provider, _ = _anthropic(create=create)
        with pytest.raises(LLMServerError):
            await provider.generate("hi", model=MODERN)

    async def test_structured_keeps_the_partial_result_too(self):
        paused = _message(text="half ", stop_reason="pause_turn")
        create = AsyncMock(side_effect=[paused, APIConnectionError("reset")])
        provider, _ = _anthropic(create=create)

        result = await provider.generate_structured("hi", model=MODERN)

        assert result.text == "half"
        assert result.stop_reason == "pause_turn"
        assert result.usage.total == 175


@pytest.mark.asyncio
class TestRefusalLogging:
    async def test_a_refusal_is_not_logged_as_a_generation_error(self):
        """A refusal is a model decision, not a provider failure."""
        from core.services.llm.providers import anthropic_provider as module

        provider, _ = _anthropic(
            create=AsyncMock(
                return_value=_message(
                    text="",
                    stop_reason="refusal",
                    stop_details=SimpleNamespace(category="safety", explanation="no"),
                )
            )
        )
        with patch.object(module.logger, "error") as error_log:
            with pytest.raises(LLMRefusalError):
                await provider.generate("hi", model=MODERN)
        error_log.assert_not_called()

    async def test_a_real_provider_failure_is_still_logged_as_an_error(self):
        from core.services.llm.providers import anthropic_provider as module

        provider, _ = _anthropic(create=AsyncMock(side_effect=APIStatusError("x", 500)))
        with patch.object(module.logger, "error") as error_log:
            with pytest.raises(LLMServerError):
                await provider.generate("hi", model=MODERN)
        assert error_log.called


@pytest.mark.asyncio
class TestAnthropicStructuredStreamStopReasons:
    """The provider *reports* the stop reason; the policy runs one layer up.

    Acting on it inside the generator would raise before ``StreamEnd``, which
    is exactly what skips the accounting for a turn that was already billed —
    so ``stream_events`` owns the policy and this layer only reports.
    """

    @staticmethod
    def _streaming_provider(final):
        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                async def gen():
                    return
                    yield  # pragma: no cover - empty event stream

                return gen()

            async def get_final_message(self):
                return final

        provider, _ = _anthropic(stream=lambda **kwargs: _Stream())
        return provider

    async def test_the_stop_reason_reaches_the_terminal_event(self):
        provider = self._streaming_provider(
            _message(text="half", stop_reason="max_tokens")
        )
        events = [e async for e in provider.generate_structured_stream("hi", MODERN)]
        assert events[-1].result.stop_reason == "max_tokens"

    async def test_a_refusal_is_reported_not_raised_here(self):
        provider = self._streaming_provider(
            _message(
                text="",
                stop_reason="refusal",
                stop_details=SimpleNamespace(category="safety", explanation="no"),
            )
        )
        events = [e async for e in provider.generate_structured_stream("hi", MODERN)]
        assert events[-1].result.stop_reason == "refusal"
        assert events[-1].result.stop_details == {
            "category": "safety",
            "explanation": "no",
        }


@pytest.mark.asyncio
class TestStreamedCacheBuckets:
    async def test_the_stream_sink_carries_the_cache_split(self):
        """A cached streamed prompt must not price as fresh input."""
        events = [
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=20,
                        output_tokens=0,
                        cache_creation_input_tokens=5,
                        cache_read_input_tokens=800,
                    )
                ),
            ),
            SimpleNamespace(type="text_delta", text="hi"),
            SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(
                    input_tokens=0,
                    output_tokens=40,
                    cache_creation_input_tokens=5,
                    cache_read_input_tokens=800,
                ),
            ),
        ]

        class _Stream:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def __aiter__(self):
                async def gen():
                    for event in events:
                        yield event

                return gen()

        provider, _ = _anthropic(stream=lambda **kwargs: _Stream())
        sink: list[Usage] = []
        chunks = [
            c
            async for c in provider.generate_stream("hi", model=MODERN, usage_sink=sink)
        ]

        assert sink[-1] == Usage(
            input_tokens=20,
            output_tokens=40,
            cache_write_tokens=5,
            cache_read_tokens=800,
        )
        # The cumulative count still covers every billed prompt token.
        assert chunks[-1] == ("", 865)
