"""The Anthropic stream contract, asserted against the SDK's own event types.

Every event here is constructed from ``anthropic.types`` /
``anthropic.lib.streaming``, never from a hand-built ``SimpleNamespace``. That
is the whole point of the module: the streaming reader used to dispatch on
``event.type == "text_delta"``, a shape the SDK has never emitted (the delta
type lives on ``event.delta.type``), and the suite blessed it because the
doubles were written to match the reader rather than the wire. A fabricated
double can agree with a broken reader forever; a real ``RawContentBlockDelta``
cannot.

The SDK stream yields the raw wire events *and* accumulated companions for the
same delta (``TextEvent``, ``InputJsonEvent``). Reading both would double every
chunk, so the reader takes the raw ones — which also carry the ``index`` a
partial-JSON delta needs to find its tool call. ``test_augmented_and_raw_...``
is the regression guard for that choice.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.lib.streaming._types import InputJsonEvent, TextEvent, ThinkingEvent
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    TextDelta,
    ToolUseBlock,
)

from core.services.llm.providers._anthropic_streaming import _content_delta


def text_delta(text: str, *, index: int = 0) -> RawContentBlockDeltaEvent:
    """Build the wire event the API sends for a text chunk."""
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=TextDelta(type="text_delta", text=text),
    )


def json_delta(partial: str, *, index: int = 0) -> RawContentBlockDeltaEvent:
    """Build the wire event the API sends for a tool-argument chunk."""
    return RawContentBlockDeltaEvent(
        type="content_block_delta",
        index=index,
        delta=InputJSONDelta(type="input_json_delta", partial_json=partial),
    )


def tool_start(call_id: str, name: str, *, index: int = 0) -> RawContentBlockStartEvent:
    """Build the wire event that opens a ``tool_use`` block."""
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(id=call_id, name=name, input={}, type="tool_use"),
    )


class _Stream:
    """Async-iterable stand-in for the SDK's stream context manager."""

    def __init__(self, events: list[Any], final: Any = None) -> None:
        self._events = events
        self._final = final

    def __aiter__(self) -> _Stream:
        self._iter = iter(self._events)
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration from None

    async def get_final_message(self) -> Any:
        return self._final


def _provider_with(events: list[Any], final: Any = None):
    """Return an ``AnthropicProvider`` whose stream replays ``events``."""
    client = MagicMock()
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=_Stream(events, final))
    manager.__aexit__ = AsyncMock()
    client.messages.stream.return_value = manager

    with patch(
        "core.services.llm.providers.anthropic_provider.anthropic"
    ) as mock_anthropic:
        mock_anthropic.AsyncAnthropic.return_value = client
        mock_anthropic.NOT_GIVEN = "not_given"
        from core.services.llm.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key="sk-ant-test")
        provider._ensure_client()
        return provider


def _final_message(text: str) -> MagicMock:
    """Minimal accumulated message for the terminal ``StreamEnd``."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    final = MagicMock()
    final.content = [block]
    final.stop_reason = "end_turn"
    final.usage = None
    return final


class TestContentDelta:
    """Unit coverage for the event normaliser."""

    def test_raw_text_delta(self) -> None:
        assert _content_delta(text_delta("Hello", index=2)) == ("text", 2, "Hello")

    def test_raw_input_json_delta(self) -> None:
        assert _content_delta(json_delta('{"q":', index=1)) == (
            "input_json",
            1,
            '{"q":',
        )

    def test_accumulated_companions_are_ignored(self) -> None:
        """``TextEvent``/``InputJsonEvent`` repeat a delta the raw event carried."""
        assert _content_delta(TextEvent(type="text", text="Hi", snapshot="Hi")) is None
        assert (
            _content_delta(
                InputJsonEvent(type="input_json", partial_json='{"q":', snapshot={})
            )
            is None
        )

    def test_thinking_and_unknown_events_are_ignored(self) -> None:
        assert (
            _content_delta(ThinkingEvent(type="thinking", thinking="hm", snapshot="hm"))
            is None
        )
        assert _content_delta(tool_start("tu_1", "lookup")) is None

    def test_bare_delta_shape_is_tolerated(self) -> None:
        """A wrapper forwarding bare delta objects still reads.

        No SDK emits this, so it cannot double up with the raw path.
        """
        assert _content_delta(TextDelta(type="text_delta", text="Hi")) == (
            "text",
            -1,
            "Hi",
        )


@pytest.mark.asyncio
class TestStreamTextContract:
    """``stream_text`` against real wire events."""

    async def test_yields_text_from_raw_content_block_deltas(self) -> None:
        provider = _provider_with([text_delta("Hello"), text_delta(" world")])

        chunks = [
            chunk
            async for chunk, _ in provider.generate_stream(
                "prompt", model="claude-sonnet-5"
            )
        ]

        assert "".join(chunks) == "Hello world"

    async def test_augmented_and_raw_events_do_not_double_emit(self) -> None:
        """The SDK sends both shapes for one chunk; the reader counts it once."""
        provider = _provider_with(
            [
                text_delta("Hello"),
                TextEvent(type="text", text="Hello", snapshot="Hello"),
            ]
        )

        chunks = [
            chunk
            async for chunk, _ in provider.generate_stream(
                "prompt", model="claude-sonnet-5"
            )
        ]

        assert "".join(chunks) == "Hello"


@pytest.mark.asyncio
class TestStreamStructuredContract:
    """``stream_structured`` against real wire events."""

    async def test_emits_text_and_tool_call_events(self) -> None:
        from core.services.llm.stream_events import (
            StreamEnd,
            ToolCallDelta,
            ToolCallStarted,
        )
        from core.services.llm.stream_events import (
            TextDelta as NeutralTextDelta,
        )

        provider = _provider_with(
            [
                text_delta("Looking", index=0),
                tool_start("tu_1", "lookup", index=1),
                json_delta('{"city":', index=1),
                json_delta('"Rome"}', index=1),
            ],
            final=_final_message("Looking"),
        )

        events = [
            event
            async for event in provider.generate_structured_stream(
                "prompt", model="claude-sonnet-5"
            )
        ]

        assert isinstance(events[0], NeutralTextDelta)
        assert events[0].text == "Looking"
        assert isinstance(events[1], ToolCallStarted)
        assert (events[1].id, events[1].name) == ("tu_1", "lookup")
        deltas = [e for e in events if isinstance(e, ToolCallDelta)]
        assert [d.arguments_delta for d in deltas] == ['{"city":', '"Rome"}']
        assert all(d.id == "tu_1" for d in deltas)
        assert isinstance(events[-1], StreamEnd)

    async def test_json_delta_without_an_open_tool_is_dropped(self) -> None:
        """An argument delta whose block never opened has no call to attribute."""
        from core.services.llm.stream_events import ToolCallDelta

        provider = _provider_with(
            [json_delta('{"city":', index=7)], final=_final_message("")
        )

        events = [
            event
            async for event in provider.generate_structured_stream(
                "prompt", model="claude-sonnet-5"
            )
        ]

        assert not [e for e in events if isinstance(e, ToolCallDelta)]
