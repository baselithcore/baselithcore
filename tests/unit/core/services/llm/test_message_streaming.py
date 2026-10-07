"""Real token streaming of the message API (``generate_messages(on_text=...)``).

A fake provider scripts exactly what a streaming server sends. What is pinned:
chunks arrive incrementally, tool calls arrive whole, usage is booked once,
failures before the first chunk fail over while failures after it never restart,
cancellation closes the provider stream, and every path that cannot stream
(kill switch, coerced tools, structured output) keeps the buffered behaviour.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from core.services.llm.exceptions import LLMProviderError, StreamInterruptedError
from core.services.llm.messages import Message
from core.services.llm.stream_events import StreamEnd, TextDelta
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
)
from core.services.llm.usage import Usage
from core.services.llm.usage_sinks import (
    UsageReport,
    register_usage_sink,
    unregister_usage_sink,
)

HISTORY = [Message.user("hi")]
TOOL = LLMToolSpec(name="clock", description="time")


class FakeStreamingProvider:
    """Scripts one streamed turn: strings are chunks, exceptions are failures."""

    supports_native_tools = True
    supports_messages = True
    supports_streaming_tools = True

    def __init__(self, script: list[Any], final: LLMResult | None = None) -> None:
        self.script = script
        self.final = final
        self.opened = 0
        self.closed = False
        self.buffered_calls = 0

    async def generate_messages(self, messages, model, **kwargs):
        self.buffered_calls += 1
        return self.final or LLMResult(text="".join(map(str, self.script)))

    async def generate_messages_stream(self, messages, model, **kwargs):
        self.opened += 1
        try:
            text = ""
            for item in self.script:
                if isinstance(item, BaseException):
                    raise item
                if item == "<hang>":
                    await asyncio.Event().wait()
                text += item
                yield TextDelta(item)
            yield StreamEnd(
                self.final
                or LLMResult(text=text, usage=Usage(input_tokens=11, output_tokens=5))
            )
        finally:
            self.closed = True

    async def close(self) -> None:
        return None


def _service(provider, *, chain: str = "", stream: bool = True):
    from core.config.services import LLMConfig
    from core.services.llm.service import LLMService

    config = LLMConfig(
        provider="ollama",
        model="m",
        fallback_chain=chain,
        stream_agent_text=stream,
    )
    with patch.object(LLMService, "_create_provider", return_value=provider):
        return LLMService(config=config, enable_cache=False)


@pytest.fixture(autouse=True)
def _no_tenant_ledger(monkeypatch):
    async def _record(usd, **_kwargs):
        return None

    monkeypatch.setattr("core.quotas.cost_enforcement.record_tenant_llm_cost", _record)


@pytest.fixture
def reports():
    seen: list[UsageReport] = []
    register_usage_sink(seen.append)
    try:
        yield seen
    finally:
        unregister_usage_sink(seen.append)


async def test_text_arrives_chunk_by_chunk_and_the_result_is_whole():
    provider = FakeStreamingProvider(["Hel", "lo", " world"])
    chunks: list[str] = []

    result = await _service(provider).generate_messages(HISTORY, on_text=chunks.append)

    assert chunks == ["Hel", "lo", " world"]
    assert result.text == "Hello world"
    assert provider.opened == 1 and provider.buffered_calls == 0
    assert provider.closed


async def test_usage_is_booked_exactly_once_with_the_providers_counts(reports):
    from core.services.llm.usage_sinks import drain_usage_sinks

    provider = FakeStreamingProvider(["a", "b", "c"])
    await _service(provider).generate_messages(HISTORY, on_text=lambda _t: None)
    await drain_usage_sinks()

    mine = [replace(r, tenant_id=None) for r in reports]
    assert len(mine) == 1
    assert mine[0].usage == Usage(input_tokens=11, output_tokens=5)


async def test_a_tool_call_after_text_arrives_whole_on_the_result():
    call = ToolCall(id="c1", name="clock", arguments={"tz": "UTC"})
    final = LLMResult(text="Let me check", tool_calls=[call], stop_reason="tool_calls")
    provider = FakeStreamingProvider(["Let me", " check"], final=final)
    chunks: list[str] = []

    result = await _service(provider).generate_messages(
        HISTORY, tools=[TOOL], on_text=chunks.append
    )

    assert "".join(chunks) == "Let me check"
    assert result.tool_calls == [call]


async def test_a_tool_call_only_turn_streams_no_text():
    call = ToolCall(id="c1", name="clock", arguments={})
    provider = FakeStreamingProvider([], final=LLMResult(tool_calls=[call]))
    chunks: list[str] = []

    result = await _service(provider).generate_messages(
        HISTORY, tools=[TOOL], on_text=chunks.append
    )

    assert chunks == []
    assert result.tool_calls == [call]


async def test_a_failure_before_the_first_chunk_fails_over_invisibly():
    primary = FakeStreamingProvider([LLMProviderError("down")])
    secondary = FakeStreamingProvider(["saved"])
    secondary_service = _service(secondary)
    chunks: list[str] = []

    with patch(
        "core.services.llm.fallback_runtime._clone_service",
        return_value=secondary_service,
    ):
        result = await _service(primary, chain="openai:gpt-4o-mini").generate_messages(
            HISTORY, on_text=chunks.append
        )

    assert chunks == ["saved"]
    assert result.text == "saved"
    assert primary.opened == 1 and secondary.opened == 1


async def test_a_failure_after_a_chunk_never_restarts_and_keeps_the_partial(reports):
    from core.services.llm.usage_sinks import drain_usage_sinks

    primary = FakeStreamingProvider(["Hel", LLMProviderError("connection reset")])
    secondary = FakeStreamingProvider(["again"])
    chunks: list[str] = []

    with patch(
        "core.services.llm.fallback_runtime._clone_service",
        return_value=_service(secondary),
    ):
        with pytest.raises(StreamInterruptedError) as caught:
            await _service(primary, chain="openai:gpt-4o-mini").generate_messages(
                HISTORY, on_text=chunks.append
            )
    await drain_usage_sinks()

    assert caught.value.partial_text == "Hel"
    assert chunks == ["Hel"]
    assert primary.opened == 1 and secondary.opened == 0
    # The spend is not invisible: one estimated record for the partial turn.
    assert len(reports) == 1


async def test_cancelling_the_caller_closes_the_provider_stream():
    provider = FakeStreamingProvider(["first", "<hang>"])
    chunks: list[str] = []
    task = asyncio.ensure_future(
        _service(provider).generate_messages(HISTORY, on_text=chunks.append)
    )
    for _ in range(200):
        if chunks:
            break
        await asyncio.sleep(0.005)
    assert chunks == ["first"]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert provider.closed


async def test_kill_switch_off_restores_the_buffered_turn():
    provider = FakeStreamingProvider(["a", "b"])
    chunks: list[str] = []

    result = await _service(provider, stream=False).generate_messages(
        HISTORY, on_text=chunks.append
    )

    assert chunks == []
    assert result.text == "ab"
    assert provider.opened == 0 and provider.buffered_calls == 1


async def test_a_provider_without_the_capability_falls_back_to_buffered():
    provider = FakeStreamingProvider(["a", "b"])
    provider.supports_streaming_tools = False
    chunks: list[str] = []

    result = await _service(provider).generate_messages(HISTORY, on_text=chunks.append)

    assert chunks == [] and result.text == "ab"
    assert provider.opened == 0


async def test_structured_output_is_never_streamed():
    provider = FakeStreamingProvider(["{}"])
    chunks: list[str] = []

    await _service(provider).generate_messages(
        HISTORY,
        response_format=ResponseFormat(name="x", schema={"type": "object"}),
        on_text=chunks.append,
    )

    assert chunks == [] and provider.opened == 0 and provider.buffered_calls == 1


async def test_coerced_tools_take_the_prompt_path_and_never_stream_text():
    """No native tool API: the model's JSON is a prompt-coerced tool call."""
    provider = FakeStreamingProvider(["{", '"tool"', "}"])
    provider.supports_native_tools = False
    provider.generate = AsyncMock(return_value=("plain answer", 7))
    chunks: list[str] = []

    result = await _service(provider).generate_messages(
        HISTORY, tools=[TOOL], on_text=chunks.append
    )

    assert chunks == [] and provider.opened == 0
    provider.generate.assert_awaited()
    assert result.text == "plain answer"


async def test_a_streamed_call_takes_the_same_rate_limit_slot_as_a_buffered_one():
    provider = FakeStreamingProvider(["never"])
    chunks: list[str] = []

    async def deny(_provider):
        raise LLMProviderError("rate limit exceeded")

    with patch("core.services.llm.message_runtime.acquire_llm_call_slot", deny):
        with pytest.raises(LLMProviderError):
            await _service(provider).generate_messages(HISTORY, on_text=chunks.append)

    assert provider.opened == 0 and chunks == []


async def test_a_tenant_over_budget_cannot_open_a_stream():
    from core.quotas.manager import CostBudgetExceededError

    provider = FakeStreamingProvider(["never"])

    async def over(**_kwargs):
        raise CostBudgetExceededError("t1", SimpleNamespace(value="daily"), 1.0, 2.0)

    with patch("core.quotas.cost_enforcement.enforce_tenant_cost_budget", over):
        with pytest.raises(CostBudgetExceededError):
            await _service(provider).generate_messages(HISTORY, on_text=lambda _t: None)

    assert provider.opened == 0


async def test_a_cancelled_stream_still_books_its_usage_exactly_once(reports):
    from core.services.llm.usage_sinks import drain_usage_sinks

    provider = FakeStreamingProvider(["some words", "<hang>"])
    chunks: list[str] = []
    task = asyncio.ensure_future(
        _service(provider).generate_messages(HISTORY, on_text=chunks.append)
    )
    for _ in range(200):
        if chunks:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await drain_usage_sinks()

    assert len(reports) == 1
    assert reports[0].usage.total > 0


async def test_a_provider_error_body_never_reaches_the_exception_or_the_logs(caplog):
    secret = "Bearer sk-live-SECRET and the user's private prompt"

    class Echoing(Exception):
        status_code = 400

    provider = FakeStreamingProvider([])

    async def broken(messages, model, **kwargs):
        raise Echoing(f"Error code: 400 - {secret}")
        yield  # pragma: no cover

    provider.generate_messages_stream = broken
    with caplog.at_level("DEBUG"):
        with pytest.raises(LLMProviderError) as caught:
            await _service(provider).generate_messages(HISTORY, on_text=lambda _t: None)
    assert "SECRET" not in str(caught.value) and "prompt" not in str(caught.value)
    assert "SECRET" not in caplog.text
