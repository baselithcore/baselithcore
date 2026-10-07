"""The OpenAI-compatible streamed turn, against SSE chunks as servers send them.

Covers what only the wire format can break: tool-call fragments by ``index``,
the terminal usage chunk, reasoning (inline across chunk boundaries, and in a
parsing server's own field) and the stream being closed on every exit.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.services.llm.messages import Message
from core.services.llm.providers._openai_messages_stream import stream_messages
from core.services.llm.reasoning_text import ReasoningStreamFilter
from core.services.llm.stream_events import StreamEnd, TextDelta
from core.services.llm.tool_calling import LLMToolSpec

HISTORY = [Message.user("hi")]


def _chunk(content=None, *, reasoning=None, tools=None, finish=None, usage=None):
    delta = SimpleNamespace(
        content=content, reasoning_content=reasoning, tool_calls=tools, refusal=None
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


def _usage_chunk(prompt=20, completion=7):
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
        prompt_tokens_details=None,
    )
    return SimpleNamespace(choices=[], usage=usage)


def _fragment(index, *, id=None, name=None, args=None):
    function = SimpleNamespace(name=name, arguments=args)
    return SimpleNamespace(index=index, id=id, function=function)


class FakeStream:
    def __init__(self, chunks, *, fail_at=None):
        self._chunks = chunks
        self._fail_at = fail_at
        self.closed = False

    def __aiter__(self):
        return self._run()

    async def _run(self):
        for position, chunk in enumerate(self._chunks):
            if position == self._fail_at:
                raise RuntimeError("socket closed")
            yield chunk

    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, stream):
        self.stream = stream
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.stream


async def _run(chunks, **kwargs):
    stream = FakeStream(chunks)
    client = FakeClient(stream)
    events = [e async for e in stream_messages(client, HISTORY, "m", **kwargs)]
    return events, client, stream


def _texts(events):
    return [e.text for e in events if isinstance(e, TextDelta)]


def _end(events):
    assert isinstance(events[-1], StreamEnd)
    return events[-1].result


async def test_chunks_become_deltas_and_the_stream_asks_for_usage():
    events, client, stream = await _run(
        [_chunk("Hel"), _chunk("lo"), _chunk(None, finish="stop"), _usage_chunk()]
    )

    assert _texts(events) == ["Hel", "lo"]
    result = _end(events)
    assert result.text == "Hello"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (20, 7)
    request = client.requests[0]
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert stream.closed


async def test_tool_call_arguments_are_assembled_never_streamed_as_text():
    events, _, _ = await _run(
        [
            _chunk("Checking"),
            _chunk(tools=[_fragment(0, id="call_1", name="clock", args='{"tz"')]),
            _chunk(tools=[_fragment(0, args=': "UTC"}')]),
            _chunk(None, finish="tool_calls"),
            _usage_chunk(),
        ],
        tools=[LLMToolSpec(name="clock", description="time")],
    )

    assert _texts(events) == ["Checking"]
    result = _end(events)
    assert [(c.id, c.name, c.arguments) for c in result.tool_calls] == [
        ("call_1", "clock", {"tz": "UTC"})
    ]
    assert result.stop_reason == "tool_calls"


async def test_parallel_tool_calls_keep_their_index_order():
    events, _, _ = await _run(
        [
            _chunk(tools=[_fragment(1, id="b", name="two", args="{}")]),
            _chunk(tools=[_fragment(0, id="a", name="one", args="{}")]),
        ]
    )
    assert [c.name for c in _end(events).tool_calls] == ["one", "two"]


async def test_a_tool_only_turn_streams_no_text_at_all():
    events, _, _ = await _run(
        [_chunk(tools=[_fragment(0, id="a", name="one", args="{}")])]
    )
    assert _texts(events) == []
    assert _end(events).text is None


async def test_leading_blank_space_is_held_until_a_visible_character():
    events, _, _ = await _run([_chunk("\n\n"), _chunk("  hi"), _chunk(" there")])
    assert _texts(events) == ["hi", " there"]
    assert _end(events).text == "hi there"


async def test_a_think_tag_split_across_chunks_is_stripped():
    guard = ReasoningStreamFilter()
    events, _, _ = await _run(
        [
            _chunk("<thi"),
            _chunk("nk>secret plan</thi"),
            _chunk("nk>\n\nan"),
            _chunk("swer"),
        ],
        guard=guard,
    )
    assert "".join(_texts(events)) == "answer"
    assert "secret" not in _end(events).text


async def test_a_template_opened_reasoner_streams_only_after_the_close_tag():
    guard = ReasoningStreamFilter()
    events, _, _ = await _run(
        [
            _chunk("Okay, the user"),
            _chunk(" wants it.</think>"),
            _chunk("Hello"),
            _chunk("!"),
        ],
        guard=guard,
    )
    assert _texts(events) == ["Hello", "!"]


async def test_an_eager_guard_streams_a_normal_answer_from_the_first_chunk():
    events, _, _ = await _run(
        [_chunk("Hello"), _chunk(" world")], guard=ReasoningStreamFilter(eager=True)
    )
    assert _texts(events) == ["Hello", " world"]


async def test_reasoning_in_a_parsing_servers_own_field_is_never_text():
    events, _, _ = await _run(
        [_chunk(None, reasoning="thinking..."), _chunk("Hi", reasoning=None)],
        guard=ReasoningStreamFilter(),
    )
    assert "".join(_texts(events)) == "Hi"


async def test_a_server_without_usage_gets_the_buffered_paths_estimate():
    events, _, _ = await _run([_chunk("hello there")])
    assert _end(events).tokens_used > 0


async def test_a_mid_stream_failure_closes_the_connection_and_propagates():
    stream = FakeStream([_chunk("a"), _chunk("b")], fail_at=1)
    client = FakeClient(stream)
    seen = []
    with pytest.raises(Exception, match="RuntimeError"):
        async for event in stream_messages(client, HISTORY, "m"):
            seen.append(event)
    assert _texts(seen) == ["a"]
    assert stream.closed


async def test_abandoning_the_generator_closes_the_connection():
    stream = FakeStream([_chunk("a"), _chunk("b")])
    client = FakeClient(stream)
    gen = stream_messages(client, HISTORY, "m")
    first = await gen.__anext__()
    assert isinstance(first, TextDelta)
    await gen.aclose()
    assert stream.closed


async def _vllm_events(model, chunks):
    from unittest.mock import AsyncMock, patch

    from core.services.llm.providers.vllm_provider import VLLMProvider

    provider = VLLMProvider(api_base="http://gpu:8002")
    stream = FakeStream(chunks)
    client = FakeClient(stream)
    with (
        patch.object(provider, "_client_for", return_value=client),
        patch.object(
            provider, "_endpoint_for", AsyncMock(return_value="http://gpu:8002/v1")
        ),
    ):
        return [e async for e in provider.generate_messages_stream(HISTORY, model)]


async def test_vllm_streams_a_plain_model_live_and_holds_a_reasoner_to_its_close_tag():
    plain = await _vllm_events("llama3.1", [_chunk("Hello"), _chunk(" world")])
    assert _texts(plain) == ["Hello", " world"]

    reasoner = await _vllm_events(
        "qwen", [_chunk("hmm "), _chunk("ok</think>"), _chunk("Hi")]
    )
    assert _texts(reasoner) == ["Hi"]
    assert _end(reasoner).text == "Hi"


def test_only_known_non_reasoners_stream_live():
    from core.services.llm.reasoning_text import can_stream_live

    assert can_stream_live("qwen2.5:7b-instruct") and can_stream_live("llama3.1")
    # Unknown or reasoning-capable names are held until the closing tag.
    assert not can_stream_live("qwen") and not can_stream_live("Qwen3-8B")
    assert not can_stream_live(None)
    assert can_stream_live("my-tune", "^my-")
