"""Native tool-calling / structured-output call for the Anthropic provider.

Split out of ``anthropic_provider`` for the module size cap, next to the
streaming bodies. Takes the provider instance as its first argument and reuses
its endpoint routing (``_messages_api``), ``pause_turn`` continuation loop
(``_create``) and usage sink, so behaviour is identical to the method that
delegates here.
"""

from __future__ import annotations

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

if TYPE_CHECKING:
    from core.services.llm.providers.anthropic_provider import AnthropicProvider

logger = get_logger(__name__)

__all__ = ["generate_structured"]


async def generate_structured(
    provider: AnthropicProvider,
    prompt: str,
    model: str,
    *,
    tools: list[LLMToolSpec] | None = None,
    tool_choice: ToolChoice | None = None,
    response_format: ResponseFormat | None = None,
    **kwargs: Any,
) -> LLMResult:
    """Generate using Anthropic's native tool-calling / structured-output API.

    Tool specs map to ``tools`` and are selected via ``tool_choice``;
    ``response_format`` maps to ``output_config.format`` (json_schema).
    ``tool_use`` content blocks are parsed back into :class:`ToolCall`.

    Args:
        provider: The owning :class:`AnthropicProvider`.
        prompt: User turn.
        model: Model name.
        tools: Tools the model may call.
        tool_choice: Selection policy (defaults to auto when tools present; a
            forced choice is relaxed on families that reject it).
        response_format: Optional structured-output constraint.
        **kwargs: Same surface as ``AnthropicProvider.generate``.

    Returns:
        LLMResult: text and/or structured tool calls, with the metered usage
        split and the stop reason the caller needs to interpret it.
    """
    try:
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "system": _build_system_param(kwargs.get("system", "")),
            **build_request_kwargs(model, kwargs),
        }
        if tools:
            create_kwargs["tools"] = _apply_tool_cache_control(
                _to_anthropic_tools(tools)
            )
            create_kwargs["tool_choice"] = _to_anthropic_tool_choice(
                resolve_tool_choice(model, tool_choice)
            )
        if response_format is not None:
            # Modern structured-outputs surface (output_config.format), not the
            # deprecated top-level output_format. Merged rather than assigned:
            # an adaptive thinking request already put the effort tier in
            # output_config, and a caller may have added keys of their own.
            output_config = dict(create_kwargs.get("output_config") or {})
            output_config["format"] = {
                "type": "json_schema",
                "schema": response_format.schema,
            }
            create_kwargs["output_config"] = output_config

        response, blocks, usage = await provider._create(kwargs, create_kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in blocks:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        # Anthropic returns parsed input; never re-parse.
                        arguments=dict(block.input or {}),
                    )
                )

        if usage.is_empty:
            usage = Usage.estimate(
                estimate_tokens(prompt, model),
                estimate_tokens("".join(text_parts), model),
            )
        provider._record_usage(kwargs, usage)

        return LLMResult(
            text="".join(text_parts).strip() or None,
            tool_calls=tool_calls,
            stop_reason=getattr(response, "stop_reason", None),
            stop_details=stop_details_from(response),
            usage=usage,
            native=True,
            raw=response,
        )

    except Exception as e:
        logger.error(f"Anthropic structured generation error: {describe_exception(e)}")
        raise map_provider_exception(e, provider="Anthropic") from e
