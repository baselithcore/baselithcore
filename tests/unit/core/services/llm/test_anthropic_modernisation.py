"""Anthropic provider: per-family request shaping.

Sampling-parameter filtering, capability-derived ``max_tokens`` defaults,
adaptive vs budget thinking, beta headers and passthrough. Everything is
driven through a fake SDK client — no network.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODERN = "claude-opus-5"
LEGACY = "claude-haiku-4-5"


def _text_response(text="ok", stop_reason="end_turn"):
    response = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = text
    response.content = [block]
    response.stop_reason = stop_reason
    response.stop_details = None
    response.usage.input_tokens = 10
    response.usage.output_tokens = 5
    response.usage.cache_creation_input_tokens = 0
    response.usage.cache_read_input_tokens = 0
    return response


def _client(response=None):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response or _text_response())
    client.beta.messages.create = AsyncMock(return_value=response or _text_response())
    return client


def _provider(client, **kwargs):
    with patch(
        "core.services.llm.providers.anthropic_provider.anthropic"
    ) as mock_anthropic:
        mock_anthropic.AsyncAnthropic.return_value = client
        mock_anthropic.NOT_GIVEN = "not_given"
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test", **kwargs)
        provider._ensure_client()
        return provider


@pytest.mark.asyncio
class TestSamplingParameters:
    async def test_no_temperature_is_injected_by_default(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=LEGACY)
        assert "temperature" not in client.messages.create.call_args.kwargs

    async def test_explicit_temperature_reaches_a_family_that_accepts_it(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=LEGACY, temperature=0.2)
        assert client.messages.create.call_args.kwargs["temperature"] == 0.2

    async def test_temperature_is_dropped_on_families_that_reject_it(self):
        # Sending it would be an HTTP 400, not a degraded answer.
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN, temperature=0.2)
        assert "temperature" not in client.messages.create.call_args.kwargs

    async def test_top_p_and_top_k_follow_the_same_rule(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN, top_p=0.9, top_k=40)
        kwargs = client.messages.create.call_args.kwargs
        assert "top_p" not in kwargs
        assert "top_k" not in kwargs

        client2 = _client()
        provider2 = _provider(client2)
        await provider2.generate("hi", model=LEGACY, top_p=0.9, top_k=40)
        kwargs2 = client2.messages.create.call_args.kwargs
        assert kwargs2["top_p"] == 0.9
        assert kwargs2["top_k"] == 40

    async def test_structured_path_drops_sampling_too(self):
        client = _client()
        provider = _provider(client)
        await provider.generate_structured("hi", model=MODERN, temperature=0.5)
        assert "temperature" not in client.messages.create.call_args.kwargs


def _empty_stream(events=()):
    class _Stream:
        def __aiter__(self):
            async def _gen():
                for event in events:
                    yield event

            return _gen()

    return _Stream()


@pytest.mark.asyncio
class TestMaxTokenDefaults:
    async def test_buffered_default_is_the_capability_value(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN)
        assert client.messages.create.call_args.kwargs["max_tokens"] == 16000

    async def test_structured_default_is_the_capability_value(self):
        client = _client()
        provider = _provider(client)
        await provider.generate_structured("hi", model=MODERN)
        assert client.messages.create.call_args.kwargs["max_tokens"] == 16000

    async def test_caller_value_wins(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN, max_tokens=512)
        assert client.messages.create.call_args.kwargs["max_tokens"] == 512

    async def test_streaming_default_is_the_larger_cap(self):
        client = MagicMock()
        stream = MagicMock()
        stream.__aenter__ = AsyncMock(return_value=_empty_stream())
        stream.__aexit__ = AsyncMock()
        client.messages.stream.return_value = stream
        provider = _provider(client)
        async for _ in provider.generate_stream("hi", model=MODERN):
            pass
        assert client.messages.stream.call_args.kwargs["max_tokens"] == 64000


@pytest.mark.asyncio
class TestThinkingPayload:
    async def test_modern_family_gets_adaptive_thinking(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN, effort="high")
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["thinking"] == {"type": "adaptive"}
        assert kwargs["output_config"] == {"effort": "high"}
        assert "temperature" not in kwargs

    async def test_legacy_family_gets_the_budget_form(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=LEGACY, effort="high")
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] > 0
        assert kwargs["temperature"] == 1.0

    async def test_thinking_temperature_overrides_the_caller(self):
        # The budget form requires temperature=1; the caller's 0.2 would 400.
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=LEGACY, effort="low", temperature=0.2)
        assert client.messages.create.call_args.kwargs["temperature"] == 1.0

    async def test_no_thinking_kwargs_without_an_effort(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN)
        assert "thinking" not in client.messages.create.call_args.kwargs


@pytest.mark.asyncio
class TestBetasAndPassthrough:
    async def test_no_betas_uses_the_stable_endpoint(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN)
        assert client.messages.create.called
        assert not client.beta.messages.create.called

    async def test_constructor_betas_route_to_the_beta_endpoint(self):
        client = _client()
        provider = _provider(client, betas=["context-1m-2025-08-07"])
        await provider.generate("hi", model=MODERN)
        assert client.beta.messages.create.called
        assert client.beta.messages.create.call_args.kwargs["betas"] == [
            "context-1m-2025-08-07"
        ]

    async def test_per_call_betas_merge_with_constructor_betas(self):
        client = _client()
        provider = _provider(client, betas=["alpha"])
        await provider.generate("hi", model=MODERN, betas=["beta"])
        sent = client.beta.messages.create.call_args.kwargs["betas"]
        assert sent == ["alpha", "beta"]

    async def test_extra_headers_and_body_are_forwarded(self):
        client = _client()
        provider = _provider(
            client, extra_headers={"x-team": "core"}, extra_body={"foo": 1}
        )
        await provider.generate("hi", model=MODERN, extra_headers={"x-call": "1"})
        kwargs = client.messages.create.call_args.kwargs
        assert kwargs["extra_headers"] == {"x-team": "core", "x-call": "1"}
        assert kwargs["extra_body"] == {"foo": 1}

    async def test_passthrough_kwargs_never_leak_into_the_request(self):
        client = _client()
        provider = _provider(client)
        await provider.generate(
            "hi", model=MODERN, effort="high", allow_refusal=True, usage_sink=[]
        )
        kwargs = client.messages.create.call_args.kwargs
        for reserved in ("effort", "allow_refusal", "usage_sink", "betas"):
            assert reserved not in kwargs


@pytest.mark.asyncio
class TestForcedToolChoice:
    async def test_forced_choice_is_downgraded_where_it_would_400(self):
        from core.services.llm.tool_calling import LLMToolSpec, ToolChoice

        client = _client()
        provider = _provider(client)
        await provider.generate_structured(
            "hi",
            model="claude-fable-5-1",
            tools=[LLMToolSpec(name="ping", description="ping")],
            tool_choice=ToolChoice(mode="any"),
        )
        assert client.messages.create.call_args.kwargs["tool_choice"] == {
            "type": "auto"
        }

    async def test_forced_choice_survives_elsewhere(self):
        from core.services.llm.tool_calling import LLMToolSpec, ToolChoice

        client = _client()
        provider = _provider(client)
        await provider.generate_structured(
            "hi",
            model=MODERN,
            tools=[LLMToolSpec(name="ping", description="ping")],
            tool_choice=ToolChoice(mode="any"),
        )
        assert client.messages.create.call_args.kwargs["tool_choice"] == {"type": "any"}


@pytest.mark.asyncio
class TestOutputConfigMerging:
    """A caller's ``output_config`` must survive, not be silently dropped."""

    async def test_caller_keys_are_kept_alongside_the_effort_tier(self):
        client = _client()
        provider = _provider(client)
        await provider.generate(
            "hi",
            model=MODERN,
            effort="high",
            output_config={"some_future_key": "value"},
        )
        sent = client.messages.create.call_args.kwargs["output_config"]
        assert sent == {"effort": "high", "some_future_key": "value"}

    async def test_the_resolved_tier_wins_over_a_caller_effort(self):
        # The plan's tier is already clamped to what the family accepts; a raw
        # caller value could be the 400 this whole layer exists to prevent.
        client = _client()
        provider = _provider(client)
        await provider.generate(
            "hi",
            model="claude-opus-4-6",
            effort="xhigh",
            output_config={"effort": "max"},
        )
        sent = client.messages.create.call_args.kwargs["output_config"]
        assert sent == {"effort": "high"}

    async def test_caller_config_survives_without_any_thinking(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model=MODERN, output_config={"k": "v"})
        assert client.messages.create.call_args.kwargs["output_config"] == {"k": "v"}

    async def test_response_format_wins_over_a_caller_format(self):
        from core.services.llm.tool_calling import ResponseFormat

        client = _client()
        provider = _provider(client)
        await provider.generate_structured(
            "hi",
            model=MODERN,
            response_format=ResponseFormat(schema={"type": "object"}),
            output_config={"format": {"type": "text"}, "keep": 1},
        )
        sent = client.messages.create.call_args.kwargs["output_config"]
        assert sent["format"]["type"] == "json_schema"
        assert sent["keep"] == 1


@pytest.mark.asyncio
class TestHaiku45Requests:
    """Haiku 4.5 takes budget thinking and never an effort tier."""

    async def test_no_output_config_effort_is_sent(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model="claude-haiku-4-5", effort="high")
        kwargs = client.messages.create.call_args.kwargs
        assert "output_config" not in kwargs
        # The tier still sizes the thinking budget, it is just never named.
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] > 0

    async def test_default_output_cap_is_the_current_one(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model="claude-haiku-4-5")
        assert client.messages.create.call_args.kwargs["max_tokens"] == 16000

    async def test_sampling_params_still_reach_it(self):
        client = _client()
        provider = _provider(client)
        await provider.generate("hi", model="claude-haiku-4-5", temperature=0.3)
        assert client.messages.create.call_args.kwargs["temperature"] == 0.3


class TestCallerEffortIsClamped:
    """A caller's raw ``output_config.effort`` is the 400 this layer prevents."""

    @staticmethod
    def _built(model, caller_effort):
        from core.services.llm.providers._anthropic_request import build_request_kwargs

        return build_request_kwargs(model, {"output_config": {"effort": caller_effort}})

    def test_a_family_without_an_effort_surface_gets_no_output_config(self):
        assert "output_config" not in self._built("claude-haiku-4-5", "max")

    def test_a_legacy_id_gets_no_output_config_either(self):
        assert "output_config" not in self._built("claude-3-5-sonnet-20240620", "high")

    def test_an_unsupported_tier_is_clamped_to_what_the_family_takes(self):
        built = self._built("claude-sonnet-4-6", "max")
        assert built["output_config"] == {"effort": "high"}

    def test_a_supported_tier_survives_untouched(self):
        built = self._built("claude-opus-5", "xhigh")
        assert built["output_config"] == {"effort": "xhigh"}

    def test_other_caller_keys_survive_the_effort_being_dropped(self):
        from core.services.llm.providers._anthropic_request import build_request_kwargs

        built = build_request_kwargs(
            "claude-haiku-4-5", {"output_config": {"effort": "max", "keep": 1}}
        )
        assert built["output_config"] == {"keep": 1}

    def test_an_uninterpretable_tier_is_dropped(self):
        assert "output_config" not in self._built("claude-opus-5", "turbo")
