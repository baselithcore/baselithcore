"""Message-API call for the Anthropic provider.

Split out of ``anthropic_provider`` for the module size cap, next to the
structured and streaming bodies. Takes the provider instance as its first
argument and reuses its endpoint routing (``_messages_api``), ``pause_turn``
continuation loop (``_create``) and usage sink, so behaviour matches the method
that delegates here.

What this path adds over ``generate_structured`` is the whole point of the
task: the conversation is sent as a **message list** rather than a rebuilt
prompt, so ``tool_use`` / ``tool_result`` blocks stay correlated by id, a
failed call is flagged with ``is_error``, thinking blocks replay verbatim —
and, because completed turns are appended rather than rewritten, the prompt
prefix is byte-stable from one iteration to the next and can be served from
the prompt cache.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.errors import map_provider_exception
from core.services.llm.exceptions import describe_exception
from core.services.llm.messages import (
    Message,
    from_anthropic_content,
    render_as_prompt,
    to_anthropic,
)
from core.services.llm.providers._anthropic_mapping import (
    _PROMPT_CACHE_ENABLED,
    _PROMPT_CACHE_MIN_CHARS,
    _apply_tool_cache_control,
    _build_system_param,
    _to_anthropic_tool_choice,
    _to_anthropic_tools,
)
from core.services.llm.providers._anthropic_request import (
    build_request_kwargs,
    resolve_tool_choice,
)
from core.services.llm.stop_reasons import stop_details_from
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)
from core.services.llm.usage import Usage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

logger = get_logger(__name__)

__all__ = ["apply_history_cache_control", "generate_messages"]


def apply_history_cache_control(
    wire: list[dict[str, Any]],
    *,
    prefix_chars: int = 0,
) -> list[dict[str, Any]]:
    """Mark the end of the conversation with an ephemeral cache breakpoint.

    The breakpoint caches *everything before it* — system prompt, tool schemas
    and every completed turn — so the next iteration, which appends to this
    exact prefix, reads the whole conversation from cache instead of re-billing
    it at full input price. It deliberately moves to the new end on every call:
    a breakpoint left behind would freeze the cache at the turn it was written.

    ``prefix_chars`` is what makes the floor check correct. Anthropic's
    cacheable minimum applies to the **cumulative** prefix, not to whichever
    segment happens to carry the breakpoint — measuring the messages alone
    means a request with a short system prompt, a large tool block and a
    mid-sized conversation (each under the floor, the sum comfortably over it)
    emits no breakpoint at all and re-bills the entire prefix every turn, which
    is precisely the cost the message loop exists to remove.

    Args:
        wire: The Anthropic ``messages`` array.
        prefix_chars: Serialized size of everything that renders *ahead of* the
            messages — the tool block and the system prompt.

    Returns:
        list[dict]: The same array, with the final content block of the final
        message marked — a copy, so the caller's history is never mutated.
    """
    if not wire or not _PROMPT_CACHE_ENABLED:
        return wire
    total = prefix_chars + sum(len(str(entry)) for entry in wire)
    if total < _PROMPT_CACHE_MIN_CHARS:
        return wire
    last = wire[-1]
    blocks = last.get("content")
    if not isinstance(blocks, list) or not blocks:
        return wire
    marked = list(wire)
    marked_blocks = list(blocks)
    marked_blocks[-1] = {**marked_blocks[-1], "cache_control": {"type": "ephemeral"}}
    marked[-1] = {**last, "content": marked_blocks}
    return marked


def _build_create_kwargs(
    model: str,
    messages: list[Message],
    *,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
    system: str | None,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Shape the ``messages.create`` request for one message-API turn."""
    system_param = _build_system_param(system or kwargs.get("system") or "")
    tool_entries = (
        _apply_tool_cache_control(_to_anthropic_tools(tools)) if tools else None
    )
    # Everything that renders ahead of the messages counts toward the cacheable
    # minimum; see ``apply_history_cache_control``.
    prefix_chars = len(str(system_param)) + (
        sum(len(str(entry)) for entry in tool_entries) if tool_entries else 0
    )
    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": apply_history_cache_control(
            to_anthropic(messages), prefix_chars=prefix_chars
        ),
        "system": system_param,
        **build_request_kwargs(model, kwargs),
    }
    if tool_entries is not None:
        create_kwargs["tools"] = tool_entries
        create_kwargs["tool_choice"] = _to_anthropic_tool_choice(
            resolve_tool_choice(model, tool_choice)
        )
    if response_format is not None:
        # Merged, not assigned: adaptive thinking may already have put an
        # effort tier in output_config, and a caller may have added keys.
        output_config = dict(create_kwargs.get("output_config") or {})
        output_config["format"] = {
            "type": "json_schema",
            "schema": response_format.schema,
        }
        create_kwargs["output_config"] = output_config
    return create_kwargs


async def generate_messages(
    provider: AnthropicProvider,
    messages: list[Message],
    model: str,
    *,
    tools: list[LLMToolSpec] | None = None,
    system: str | None = None,
    **kwargs: Any,
) -> LLMResult:
    """Run one turn of the Anthropic Messages API from a neutral history.

    Args:
        provider: The owning :class:`AnthropicProvider`.
        messages: Conversation so far, oldest first.
        model: Model name.
        tools: Tools the model may call.
        system: System prompt; carries the prompt-cache breakpoint.
        **kwargs: ``tool_choice``, ``response_format``, and the same surface as
            ``AnthropicProvider.generate``.

    Returns:
        LLMResult: text and/or structured tool calls, the metered usage split,
        the stop reason, and ``message`` — the assistant turn verbatim, so the
        caller can append it to the history without losing thinking blocks.
    """
    tool_choice: ToolChoice | None = kwargs.pop("tool_choice", None)
    response_format: ResponseFormat | None = kwargs.pop("response_format", None)
    try:
        create_kwargs = _build_create_kwargs(
            model,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            system=system,
            kwargs=kwargs,
        )
        response, blocks, usage = await provider._create(kwargs, create_kwargs)

        turn = from_anthropic_content(blocks)
        text = "".join(
            block.text for block in blocks if getattr(block, "type", None) == "text"
        ).strip()
        tool_calls = [
            ToolCall(id=use.id, name=use.name, arguments=dict(use.input))
            for use in turn.tool_uses
        ]

        if usage.is_empty:
            usage = Usage.estimate(
                estimate_tokens(render_as_prompt(messages), model),
                estimate_tokens(text, model),
            )
        provider._record_usage(kwargs, usage)

        logger.debug(
            "anthropic_messages_turn",
            extra={
                "model": model,
                "turns": len(messages),
                "tool_calls": len(tool_calls),
            },
        )
        return LLMResult(
            text=text or None,
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", None),
            stop_details=stop_details_from(response),
            usage=usage,
            native=True,
            raw=response,
            message=turn,
        )

    except Exception as e:
        logger.error(f"Anthropic message generation error: {describe_exception(e)}")
        raise map_provider_exception(e, provider="Anthropic") from e
