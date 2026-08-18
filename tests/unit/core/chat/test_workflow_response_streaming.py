"""Streaming behaviour of ResponseMixin.generate_answer_stream.

Regression: the sync-iterator branch used to materialize the whole generation
with ``list(...)`` before yielding, so time-to-first-token degraded to
full-generation latency. Chunks must now flush as they are produced.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from core.chat.workflow_response import ResponseGenerator


class _State:
    """Minimal AgentState stand-in for the streaming path."""

    def __init__(self) -> None:
        self.user_query = "q"
        self.context = "ctx"
        self.history_text = ""
        self.plugin_data: dict = {}
        self.answer = ""
        self.next_action = ""


def _mixin(stream_fn) -> ResponseGenerator:
    return ResponseGenerator(
        MagicMock(),
        build_prompt_fn=lambda *a, **k: "prompt",
        generate_response_fn=lambda *a, **k: "unused",
        generate_response_stream_fn=stream_fn,
    )


@pytest.mark.asyncio
async def test_sync_iterator_chunks_flush_before_generation_completes():
    """The first chunk must arrive while the sync generator is still running."""
    produced_all = asyncio.Event()

    def slow_sync_stream(prompt, model=None):
        yield "first"
        # Block long enough that a buffering implementation could not have
        # yielded "first" yet by the time the consumer receives it.
        import time

        time.sleep(0.3)
        yield "second"
        produced_all.set()

    mixin = _mixin(slow_sync_stream)
    state = _State()

    agen = mixin.generate_answer_stream(state)
    first = await asyncio.wait_for(agen.__anext__(), timeout=1.0)

    assert first == "first"
    # The generator has not finished producing — proof we did not buffer.
    assert not produced_all.is_set()

    rest = [chunk async for chunk in agen]
    assert rest == ["second"]
    assert state.answer == "firstsecond"


@pytest.mark.asyncio
async def test_sync_iterator_propagates_exceptions():
    """An error raised inside the sync generator surfaces to the consumer."""

    def failing_sync_stream(prompt, model=None):
        yield "partial"
        raise RuntimeError("generator blew up")

    mixin = _mixin(failing_sync_stream)
    state = _State()

    chunks = []
    with pytest.raises(RuntimeError, match="generator blew up"):
        async for chunk in mixin.generate_answer_stream(state):
            chunks.append(chunk)

    assert chunks == ["partial"]


@pytest.mark.asyncio
async def test_async_generator_branch_still_streams():
    """The async-generator branch is unchanged and still yields incrementally."""

    async def async_stream(prompt, model=None):
        yield "a"
        yield "b"

    mixin = _mixin(async_stream)
    state = _State()

    chunks = [chunk async for chunk in mixin.generate_answer_stream(state)]
    assert chunks == ["a", "b"]
    assert state.answer == "ab"


@pytest.mark.asyncio
async def test_generated_answer_is_stored_in_response_cache():
    """The response cache must gain a writer: check_cache computes the key,
    generation stores under it, so a repeat of the same (query, context) can
    replay without paying the LLM again."""
    from unittest.mock import AsyncMock

    service = MagicMock()
    service.response_cache = MagicMock()
    service.response_cache.set = AsyncMock()

    async def fake_generate(prompt, model=None):
        return "the answer"

    gen = ResponseGenerator(
        service,
        build_prompt_fn=lambda *a, **k: "prompt",
        generate_response_fn=fake_generate,
        generate_response_stream_fn=lambda *a, **k: iter(()),
    )
    state = _State()
    state.cache_key = ("normalized query", "ctxhash")

    await gen.generate_answer(state)

    assert state.answer == "the answer"
    service.response_cache.set.assert_awaited_once_with(
        ("normalized query", "ctxhash"), "the answer"
    )
