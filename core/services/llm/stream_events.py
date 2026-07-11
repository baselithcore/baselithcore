"""Streaming generation with native tool-calling — neutral event surface.

``generate_response_stream`` yields plain text chunks; the structured path
(``generate``) returns tool calls but only after the full response. This
module closes the gap: :func:`generate_stream_events` streams a **neutral
event sequence** so agent UIs can render text deltas and show tool
invocations as the model emits them.

Events (in order): zero or more :class:`TextDelta` / :class:`ToolCallStarted`
/ :class:`ToolCallDelta`, then exactly one :class:`StreamEnd` carrying the
authoritative :class:`~core.services.llm.tool_calling.LLMResult` (parsed tool
calls, tokens, stop reason). Consumers that only need the final result can
ignore everything but ``StreamEnd``.

Routing mirrors ``generate()``: the provider's native streaming tool API is
used when ``enable_native_tools`` is on AND the provider implements
``generate_structured_stream``; otherwise the non-streaming structured path
runs and its result is replayed as a buffered event sequence — the consumer
contract is identical either way. Deadline enforcement
(``stream_within_deadline``) and token/cost accounting match the sibling
paths.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm._deadline import stream_within_deadline
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.tool_calling import LLMResult, LLMToolSpec, ToolChoice

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Incremental assistant text."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """The model began emitting a tool invocation."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """Partial JSON of a tool call's arguments (render-only; never parse
    incrementally — the parsed arguments arrive on ``StreamEnd``)."""

    id: str
    arguments_delta: str


@dataclass(frozen=True, slots=True)
class StreamEnd:
    """Terminal event: the authoritative result for the whole turn."""

    result: LLMResult


StreamEvent = TextDelta | ToolCallStarted | ToolCallDelta | StreamEnd


async def generate_stream_events(
    service: LLMService,
    prompt: str,
    *,
    model: str | None = None,
    tools: list[LLMToolSpec] | None = None,
    tool_choice: ToolChoice | None = None,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[StreamEvent]:
    """Stream a structured generation as neutral events (see module doc)."""
    import time

    model = service._resolve_model(model)
    native_enabled = getattr(service.config, "enable_native_tools", False) is True
    provider_stream = getattr(service.provider, "generate_structured_stream", None)
    use_native = (
        native_enabled
        and getattr(service.provider, "supports_native_tools", False) is True
        and provider_stream is not None
    )

    if not use_native:
        # Buffered fallback: the non-streaming structured path already does
        # full span/token/budget accounting — replay its outcome as events.
        result = await service.generate(
            prompt,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if result.text:
            yield TextDelta(result.text)
        for call in result.tool_calls:
            yield ToolCallStarted(call.id, call.name)
        yield StreamEnd(result)
        return

    # Native path: accounting mirrors structured.generate_structured.
    input_tokens = await estimate_tokens_async(prompt)
    report_tokens_to_middleware(input_tokens, model="input")
    if service.cost_tracker:
        service.cost_tracker.track_tokens(input_tokens, model="input")

    extra: dict[str, Any] = {}
    if system_prompt:
        extra["system"] = system_prompt
    if temperature is not None:
        extra["temperature"] = temperature
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens

    assert provider_stream is not None  # guaranteed by use_native above
    started = time.perf_counter()
    final: LLMResult | None = None
    async for event in stream_within_deadline(
        provider_stream(prompt, model, tools=tools, tool_choice=tool_choice, **extra)
    ):
        if isinstance(event, StreamEnd):
            final = event.result
        yield event

    if final is not None:
        output_tokens = max(final.tokens_used - input_tokens, 0)
        report_tokens_to_middleware(output_tokens, model=model)
        if service.cost_tracker:
            service.cost_tracker.track_tokens(output_tokens, model=model)
        record_genai_metrics(
            gen_ai_system(service.config.provider),
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_seconds=time.perf_counter() - started,
        )
        # Charge real dollar cost against the ambient per-request LoopBudget
        # (no-op outside an orchestrated request). Lazy import: circular.
        from core.orchestration.budget_context import charge_llm_cost

        charge_llm_cost(model, input_tokens, output_tokens)


__all__ = [
    "StreamEnd",
    "StreamEvent",
    "TextDelta",
    "ToolCallDelta",
    "ToolCallStarted",
    "generate_stream_events",
]
