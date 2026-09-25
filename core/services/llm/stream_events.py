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

from core.lifecycle.deterministic import get_llm_override_kwargs
from core.observability.logging import get_logger
from core.services.llm._accounting import charge_usage_to_budget, record_usage_cost
from core.services.llm._deadline import stream_within_deadline
from core.services.llm._telemetry import (
    gen_ai_system,
    record_genai_metrics,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import estimate_tokens_async
from core.services.llm.stop_reasons import apply_stop_reason
from core.services.llm.tool_calling import LLMResult, LLMToolSpec, ToolChoice
from core.services.llm.usage import billed_usage

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
    allow_refusal: bool = False,
) -> AsyncIterator[StreamEvent]:
    """Stream a structured generation as neutral events (see module doc).

    ``allow_refusal`` has the same meaning as on the buffered paths: when
    False (the default) a refusal raises
    :class:`~core.services.llm.errors.LLMRefusalError` once the turn is
    accounted for, rather than reaching the consumer as an empty answer.
    """
    import time

    from core.services.llm._late_binding import governed_target

    # A funnel-issued service answers for whoever is calling now.
    service = governed_target(service)
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
            allow_refusal=allow_refusal,
        )
        if result.text:
            yield TextDelta(result.text)
        for call in result.tool_calls:
            yield ToolCallStarted(call.id, call.name)
        yield StreamEnd(result)
        return

    # Native path: accounting mirrors structured.generate_structured — which
    # means the pre-call tenant gate too. The buffered branch above gets it
    # from ``service.generate``; this branch talks to the provider directly,
    # so without it an event-streaming deployment spends unchecked.
    from core.quotas.cost_enforcement import enforce_tenant_cost_budget

    await enforce_tenant_cost_budget(model=model)

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
    if allow_refusal:
        extra["allow_refusal"] = True
    # CORE_DETERMINISTIC_MODE pins sampling on the event stream too.
    extra.update(get_llm_override_kwargs(service.config.provider))

    assert provider_stream is not None  # guaranteed by use_native above
    started = time.perf_counter()
    # The terminal event is held back until the turn is accounted for and the
    # stop-reason policy has run: it carries the authoritative result, so
    # ``truncated`` must already be set when the consumer sees it, and a
    # refusal must raise *instead of* delivering a result.
    final_event: StreamEnd | None = None
    final: LLMResult | None = None
    async for event in stream_within_deadline(
        provider_stream(prompt, model, tools=tools, tool_choice=tool_choice, **extra)
    ):
        if isinstance(event, StreamEnd):
            final_event = event
            final = event.result
            continue
        yield event

    if final is not None:
        # The middleware ledger already booked the estimated prompt side, so
        # only the remainder of the total may be added there; pricing and
        # metrics use the provider's metered split when it reported one.
        output_tokens = max(final.tokens_used - input_tokens, 0)
        billed = billed_usage(
            final.usage,
            fallback_input=input_tokens,
            fallback_total=final.tokens_used,
        )
        report_tokens_to_middleware(output_tokens, model=model)
        if service.cost_tracker:
            service.cost_tracker.track_tokens(output_tokens, model=model)
        record_genai_metrics(
            gen_ai_system(service.config.provider),
            model,
            input_tokens=billed.input_tokens,
            output_tokens=billed.output_tokens,
            cache_read_tokens=billed.cache_read_tokens,
            cache_write_tokens=billed.cache_write_tokens,
            duration_seconds=time.perf_counter() - started,
        )
        # Charge real dollar cost against the ambient per-request LoopBudget
        # (no-op outside an orchestrated request), every bucket at its own
        # rate, then book the turn on the tenant's cumulative ledger — the
        # ledger the pre-call gate above reads on the next call.
        charge_usage_to_budget(model, billed)
        await record_usage_cost(model, billed)

        # Stop-reason policy last: the turn is accounted for either way (it was
        # billed), and only then may a refusal abort the consumer.
        apply_stop_reason(final, model=model, allow_refusal=allow_refusal)

    if final_event is not None:
        yield final_event


__all__ = [
    "StreamEnd",
    "StreamEvent",
    "TextDelta",
    "ToolCallDelta",
    "ToolCallStarted",
    "generate_stream_events",
]
