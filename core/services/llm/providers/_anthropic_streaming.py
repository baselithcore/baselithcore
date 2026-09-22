"""Streaming generation for the Anthropic provider.

Split out of ``anthropic_provider`` for the module size cap, following the
same pattern the service layer uses (``_streaming`` next to ``service``).
Both functions take the provider instance as their first argument and reuse
its endpoint routing (``_messages_api``) and usage sink, so behaviour is
identical to the methods that delegate to them.

Usage accounting is the substantive difference from the pre-modernisation
code: a text stream never read the usage events at all, so the token count a
caller saw was a tokenizer estimate from first chunk to last. Anthropic sends
the prompt-side figure on ``message_start`` and the billed output figure on
``message_delta``; both are picked up here, and the correction reaches the
consumer as a final empty chunk — the same shape OpenAI's terminal usage
chunk already has.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.errors import map_provider_exception
from core.services.llm.exceptions import describe_exception
from core.services.llm.providers._anthropic_mapping import (
    _apply_tool_cache_control,
    _build_system_param,
    _to_anthropic_tool_choice,
    _to_anthropic_tools,
)
from core.services.llm.providers._anthropic_request import (
    build_request_kwargs,
    forwardable_kwargs,
    resolve_tool_choice,
)
from core.services.llm.stop_reasons import stop_details_from
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ToolCall,
    ToolChoice,
)
from core.services.llm.usage import Usage

if TYPE_CHECKING:
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

logger = get_logger(__name__)

__all__ = ["stream_structured", "stream_text"]


def _content_delta(event: Any) -> tuple[str, int, str] | None:
    """Normalise one stream event into ``(kind, block_index, payload)``.

    ``kind`` is ``"text"`` or ``"input_json"``; anything else returns ``None``.

    The SDK's stream helper yields the raw wire events *and* its own
    accumulated companions for the same delta (``TextEvent`` with
    ``type="text"``, ``InputJsonEvent`` with ``type="input_json"``). Only the
    raw ``content_block_delta`` is read here, for two reasons: it is the event
    that carries ``index``, without which a partial-JSON delta cannot be
    attributed to the tool call it belongs to, and reading both shapes would
    emit every text chunk twice.

    A *top-level* ``text_delta`` / ``input_json_delta`` type is accepted as
    well. No Anthropic SDK sends that shape — the delta type lives on
    ``event.delta.type``, never on ``event.type`` — so it cannot double up
    with the raw path; it is honoured because a hand-built double or a wrapper
    that forwards bare delta objects does produce it.

    Args:
        event: One item from the provider stream iterator.

    Returns:
        The normalised delta, or ``None`` for events that carry no content.
    """
    etype = getattr(event, "type", "")
    if etype == "content_block_delta":
        delta = getattr(event, "delta", None)
        dtype = getattr(delta, "type", "")
        index = getattr(event, "index", -1)
        if dtype == "text_delta":
            return ("text", index, getattr(delta, "text", "") or "")
        if dtype == "input_json_delta":
            return ("input_json", index, getattr(delta, "partial_json", "") or "")
        return None
    if etype == "text_delta":
        return ("text", getattr(event, "index", -1), getattr(event, "text", "") or "")
    if etype == "input_json_delta":
        return (
            "input_json",
            getattr(event, "index", -1),
            getattr(event, "partial_json", "") or "",
        )
    return None


async def stream_structured(
    provider: AnthropicProvider,
    prompt: str,
    model: str,
    *,
    tools: list[LLMToolSpec] | None = None,
    tool_choice: ToolChoice | None = None,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Stream a structured generation as neutral ``StreamEvent``s.

    Emits ``TextDelta`` per text chunk, ``ToolCallStarted`` / ``ToolCallDelta``
    while the model writes a tool invocation, then a terminal ``StreamEnd``
    built from the SDK's accumulated final message (parsed tool inputs, exact
    usage) — identical ``LLMResult`` shape to ``generate_structured``.
    """
    # Lazy: stream_events imports the service layer (avoid import cycle).
    from core.services.llm.stream_events import (
        StreamEnd,
        TextDelta,
        ToolCallDelta,
        ToolCallStarted,
    )

    api, extra = provider._messages_api(kwargs)
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "system": _build_system_param(kwargs.get("system", "")),
        **build_request_kwargs(model, kwargs, streaming=True),
    }
    if tools:
        create_kwargs["tools"] = _apply_tool_cache_control(_to_anthropic_tools(tools))
        create_kwargs["tool_choice"] = _to_anthropic_tool_choice(
            resolve_tool_choice(model, tool_choice)
        )

    try:
        async with api.stream(**create_kwargs, **extra) as stream:
            # Track tool_use block ids by content-block index so the
            # partial-JSON deltas can be attributed to their call.
            open_tools: dict[int, str] = {}
            async for event in stream:
                # The SDK stream yields a wide event union; getattr-based
                # dispatch keeps this tolerant of SDK additions.
                ev: Any = event
                etype = getattr(ev, "type", "")
                if etype == "content_block_start":
                    block = getattr(ev, "content_block", None)
                    if block is not None and getattr(block, "type", "") == "tool_use":
                        open_tools[getattr(ev, "index", -1)] = block.id
                        yield ToolCallStarted(id=block.id, name=block.name)
                    continue
                delta = _content_delta(ev)
                if delta is None:
                    continue
                kind, index, payload = delta
                if kind == "text":
                    if payload:
                        yield TextDelta(payload)
                    continue
                call_id = open_tools.get(index)
                if call_id is not None:
                    yield ToolCallDelta(id=call_id, arguments_delta=payload)

            final = await stream.get_final_message()

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in final.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=dict(block.input or {}),
                    )
                )
        usage = Usage.from_anthropic(getattr(final, "usage", None))
        provider._record_usage(kwargs, usage)
        # The stop reason and its details are *reported*, not acted on: the
        # refusal/truncation policy runs once, in ``stream_events``, which is
        # the layer that can account for the (billed) turn before a refusal
        # aborts the consumer. Raising from inside this generator would skip
        # that accounting entirely.
        yield StreamEnd(
            LLMResult(
                text="".join(text_parts).strip() or None,
                tool_calls=tool_calls,
                stop_reason=getattr(final, "stop_reason", None),
                stop_details=stop_details_from(final),
                usage=usage,
                native=True,
                raw=final,
            )
        )
    except Exception as e:
        logger.error(f"Anthropic structured streaming error: {describe_exception(e)}")
        raise map_provider_exception(e, provider="Anthropic", action="streaming") from e


def _merge_prompt_and_output(prompt_usage: Usage, final_usage: Usage) -> Usage:
    """Combine the stream's two usage events into one billed record.

    ``message_start`` carries the prompt side (fresh input plus both cache
    buckets); ``message_delta`` carries the final output count and repeats the
    cumulative cache figures. Taking the larger of each keeps a record that is
    correct whichever event the API populated.

    Args:
        prompt_usage: What ``message_start`` reported, if anything.
        final_usage: What ``message_delta`` reported.

    Returns:
        Usage: The per-bucket record for the whole turn.
    """
    return Usage(
        input_tokens=max(prompt_usage.input_tokens, final_usage.input_tokens),
        output_tokens=final_usage.output_tokens,
        cache_read_tokens=max(
            prompt_usage.cache_read_tokens, final_usage.cache_read_tokens
        ),
        cache_write_tokens=max(
            prompt_usage.cache_write_tokens, final_usage.cache_write_tokens
        ),
    )


async def stream_text(
    provider: AnthropicProvider, prompt: str, model: str, **kwargs: Any
) -> AsyncIterator[tuple[str, int]]:
    """Stream plain text as ``(chunk, cumulative_tokens)`` pairs.

    The count starts from a local estimate and switches to the provider's
    metered figures as ``message_start`` and ``message_delta`` arrive, so the
    final value is what was actually billed rather than a tokenizer guess.
    """
    api, extra = provider._messages_api(kwargs)
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "system": _build_system_param(kwargs.get("system", "")),
        **build_request_kwargs(model, kwargs, streaming=True),
        **forwardable_kwargs(kwargs),
    }
    try:
        async with api.stream(**create_kwargs, **extra) as stream:
            # Estimate prompt tokens once; accumulate per-delta instead of
            # re-tokenizing the full accumulated text on every chunk (which is
            # O(n^2) over the stream).
            prompt_tokens = estimate_tokens(prompt, model)
            output_tokens = 0
            # The prompt-side record from ``message_start``, kept whole: its
            # cache buckets price at a tenth (read) and 1.25x (write) of fresh
            # input, so collapsing them into one number misprices the turn.
            prompt_usage = Usage()
            async for chunk in stream:
                ctype = getattr(chunk, "type", "")
                if ctype == "message_start":
                    # Exact prompt-side usage, cache buckets included.
                    started = Usage.from_anthropic(
                        getattr(getattr(chunk, "message", None), "usage", None)
                    )
                    if not started.is_empty:
                        prompt_usage = started
                        prompt_tokens = started.prompt_tokens
                elif ctype == "message_delta":
                    final_usage = Usage.from_anthropic(getattr(chunk, "usage", None))
                    if final_usage.output_tokens:
                        billed = _merge_prompt_and_output(prompt_usage, final_usage)
                        prompt_tokens = billed.prompt_tokens
                        output_tokens = billed.output_tokens
                        provider._record_usage(kwargs, billed)
                        # Carry the correction to the consumer without
                        # inventing text.
                        yield "", billed.total
                    continue
                delta = _content_delta(chunk)
                if delta is None or delta[0] != "text" or not delta[2]:
                    continue
                text = delta[2]
                output_tokens += estimate_tokens(text, model)
                yield text, prompt_tokens + output_tokens

    except Exception as e:
        logger.error(f"Anthropic streaming error: {describe_exception(e)}")
        raise map_provider_exception(e, provider="Anthropic", action="streaming") from e
