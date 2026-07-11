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

    provider = AnthropicProvider.__new__(AnthropicProvider)  # skip __init__

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
