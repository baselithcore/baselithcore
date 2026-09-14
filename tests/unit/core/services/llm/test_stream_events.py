"""Streaming with native tool calls: neutral event surface."""

from types import SimpleNamespace

import pytest

from core.services.llm.stream_events import (
    StreamEnd,
    TextDelta,
    ToolCallDelta,
    ToolCallStarted,
    generate_stream_events,
)
from core.services.llm.tool_calling import LLMResult, LLMToolSpec, ToolCall

TOOLS = [
    LLMToolSpec(name="search", description="Search", parameters={"type": "object"})
]


class NativeStreamProvider:
    supports_native_tools = True

    def __init__(self, events):
        self._events = events
        self.calls = []

    async def generate_structured_stream(self, prompt, model, **kwargs):
        self.calls.append((prompt, model, kwargs))
        for event in self._events:
            yield event


class NoStreamProvider:
    supports_native_tools = True  # native, but no streaming API


def _service(provider, *, native=True):
    async def fake_generate(prompt, **kwargs):
        return LLMResult(
            text="buffered answer",
            tool_calls=[ToolCall(id="c1", name="search", arguments={"q": "x"})],
            tokens_used=30,
        )

    service = SimpleNamespace(
        provider=provider,
        config=SimpleNamespace(provider="anthropic", enable_native_tools=native),
        cost_tracker=None,
        generate=fake_generate,
        _resolve_model=lambda model: model or "claude-opus-4-8",
    )
    return service


async def test_native_path_streams_events_in_order():
    events = [
        TextDelta("Thinking… "),
        ToolCallStarted(id="t1", name="search"),
        ToolCallDelta(id="t1", arguments_delta='{"q": '),
        ToolCallDelta(id="t1", arguments_delta='"tokyo"}'),
        StreamEnd(
            LLMResult(
                text="Thinking…",
                tool_calls=[ToolCall(id="t1", name="search", arguments={"q": "tokyo"})],
                tokens_used=100,
            )
        ),
    ]
    provider = NativeStreamProvider(events)
    service = _service(provider)

    got = [e async for e in generate_stream_events(service, "find tokyo", tools=TOOLS)]

    assert [type(e).__name__ for e in got] == [
        "TextDelta",
        "ToolCallStarted",
        "ToolCallDelta",
        "ToolCallDelta",
        "StreamEnd",
    ]
    assert got[-1].result.tool_calls[0].arguments == {"q": "tokyo"}
    # Provider got the tools forwarded.
    assert provider.calls[0][2]["tools"] is TOOLS


async def test_flag_off_uses_buffered_fallback():
    provider = NativeStreamProvider([])  # would stream if consulted
    service = _service(provider, native=False)

    got = [e async for e in generate_stream_events(service, "q", tools=TOOLS)]

    assert provider.calls == []  # native stream never touched
    assert isinstance(got[0], TextDelta) and got[0].text == "buffered answer"
    assert isinstance(got[1], ToolCallStarted) and got[1].name == "search"
    assert isinstance(got[-1], StreamEnd)
    assert got[-1].result.text == "buffered answer"


async def test_provider_without_stream_api_falls_back():
    service = _service(NoStreamProvider(), native=True)
    got = [e async for e in generate_stream_events(service, "q")]
    assert isinstance(got[-1], StreamEnd)
    assert got[-1].result.text == "buffered answer"


# ---------------------------------------------------------------------------
# Anthropic provider event mapping (mocked SDK stream)
# ---------------------------------------------------------------------------


class FakeSDKStream:
    """Mimics anthropic AsyncMessageStream: async CM + iteration + final."""

    def __init__(self, events, final):
        self._events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __aiter__(self):
        async def gen():
            for e in self._events:
                yield e

        return gen()

    async def get_final_message(self):
        return self._final


async def test_anthropic_provider_maps_sdk_events(monkeypatch):
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

    # Fully constructed (no network: the fake client below replaces the SDK's
    # before any call is made).
    provider = AnthropicProvider(api_key="sk-ant-test")

    sdk_events = [
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="tool_use", id="tu1", name="search"),
        ),
        SimpleNamespace(type="text_delta", text="Let me look. "),
        SimpleNamespace(type="input_json_delta", index=1, partial_json='{"q":"x"}'),
    ]
    final = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="Let me look."),
            SimpleNamespace(type="tool_use", id="tu1", name="search", input={"q": "x"}),
        ],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=20,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        stop_reason="tool_use",
    )
    fake_client = SimpleNamespace(
        messages=SimpleNamespace(
            stream=lambda **kwargs: FakeSDKStream(sdk_events, final)
        )
    )
    provider.client = fake_client

    got = [
        e
        async for e in provider.generate_structured_stream(
            "find x", "claude-opus-4-8", tools=TOOLS
        )
    ]

    kinds = [type(e).__name__ for e in got]
    assert kinds == ["ToolCallStarted", "TextDelta", "ToolCallDelta", "StreamEnd"]
    end = got[-1]
    assert end.result.tool_calls == [
        ToolCall(id="tu1", name="search", arguments={"q": "x"})
    ]
    assert end.result.tokens_used == 30
    assert end.result.stop_reason == "tool_use"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# Stop-reason policy on the streamed path
# ---------------------------------------------------------------------------


async def test_streamed_max_tokens_marks_the_result_truncated():
    """A streamed answer cut off by the cap must say so, like a buffered one."""
    provider = NativeStreamProvider(
        [StreamEnd(LLMResult(text="half an ans", stop_reason="max_tokens"))]
    )
    got = [e async for e in generate_stream_events(_service(provider), "hi")]
    assert got[-1].result.truncated is True


async def test_streamed_refusal_follows_the_same_policy():
    from core.services.llm.errors import LLMRefusalError

    provider = NativeStreamProvider(
        [
            StreamEnd(
                LLMResult(
                    stop_reason="refusal",
                    stop_details={"category": "safety", "explanation": "no"},
                )
            )
        ]
    )
    with pytest.raises(LLMRefusalError):
        _ = [e async for e in generate_stream_events(_service(provider), "hi")]


async def test_streamed_refusal_can_be_allowed():
    provider = NativeStreamProvider([StreamEnd(LLMResult(stop_reason="refusal"))])
    got = [
        e
        async for e in generate_stream_events(
            _service(provider), "hi", allow_refusal=True
        )
    ]
    assert got[-1].result.stop_reason == "refusal"
    # The flag reaches the provider too, so it does not raise on the wire.
    assert provider.calls[-1][2]["allow_refusal"] is True


async def test_a_streamed_refusal_is_accounted_for_before_it_raises():
    """A refusal was generated and billed — the spend must not vanish."""
    from unittest.mock import patch

    from core.services.llm.errors import LLMRefusalError

    provider = NativeStreamProvider(
        [StreamEnd(LLMResult(stop_reason="refusal", tokens_used=120))]
    )
    charged: list[tuple] = []
    with patch(
        "core.orchestration.budget_context.charge_llm_cost",
        side_effect=lambda *a, **kw: charged.append(a),
    ):
        with pytest.raises(LLMRefusalError):
            _ = [e async for e in generate_stream_events(_service(provider), "hi")]

    assert charged, "a billed refusal must still charge the turn"


async def test_the_truncation_flag_is_set_before_stream_end_reaches_the_consumer():
    provider = NativeStreamProvider(
        [StreamEnd(LLMResult(text="half", stop_reason="max_tokens"))]
    )
    seen: list[bool] = []
    async for event in generate_stream_events(_service(provider), "hi"):
        if isinstance(event, StreamEnd):
            seen.append(event.result.truncated)
    assert seen == [True]


async def test_truncation_is_logged_once_not_twice():
    """The policy runs in exactly one place on the streamed path."""
    from unittest.mock import patch

    from core.services.llm import stop_reasons

    provider = NativeStreamProvider(
        [StreamEnd(LLMResult(text="half", stop_reason="max_tokens"))]
    )
    with patch.object(stop_reasons.logger, "warning") as warn:
        _ = [e async for e in generate_stream_events(_service(provider), "hi")]
    truncation_logs = [
        call for call in warn.call_args_list if call.args[0] == "llm_response_truncated"
    ]
    assert len(truncation_logs) == 1
