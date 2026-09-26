"""Message-API call for the OpenAI provider.

OpenAI's Chat Completions already *is* a message API; what this path adds is
the neutral history — one place that decides how a turn carrying several tool
results becomes several ``tool`` messages, how the assistant's ``tool_calls``
are replayed, and how a failed call stays visibly failed on a wire format with
no ``is_error`` field (see :mod:`core.services.llm._message_mapping`).

Prompt caching on OpenAI is automatic and prefix-based rather than declared, so
there is no breakpoint to place here: the win comes from the same property the
Anthropic path relies on — completed turns are appended, never rewritten, so
the prefix is stable from one iteration to the next.

Split from ``openai_provider`` for the module size cap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.errors import map_provider_exception
from core.services.llm.exceptions import describe_exception
from core.services.llm.messages import Message, render_as_prompt, to_openai
from core.services.llm.providers._openai_mapping import (
    response_format_kwarg,
    result_from_completion,
    to_openai_tool_choice,
    to_openai_tools,
)
from core.services.llm.providers._openai_request import token_cap
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolChoice,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.providers.openai_provider import OpenAIProvider

logger = get_logger(__name__)

__all__ = ["generate_messages"]


async def generate_messages(
    provider: OpenAIProvider,
    messages: list[Message],
    model: str,
    *,
    tools: list[LLMToolSpec] | None = None,
    system: str | None = None,
    **kwargs: Any,
) -> LLMResult:
    """Run one Chat Completions turn from a neutral message history.

    Args:
        provider: The owning :class:`OpenAIProvider`.
        messages: Conversation so far, oldest first.
        model: Model name.
        tools: Tools the model may call.
        system: System prompt, sent as the leading ``system`` message.
        **kwargs: ``tool_choice``, ``response_format``, ``temperature``,
            ``max_tokens`` (translated to ``max_completion_tokens``).

    Returns:
        LLMResult: text and/or structured tool calls, the metered usage split,
        and ``message`` — the assistant turn as neutral blocks, for replay on
        the next iteration.
    """
    tool_choice: ToolChoice | None = kwargs.pop("tool_choice", None)
    response_format: ResponseFormat | None = kwargs.pop("response_format", None)
    client = provider._ensure_client()
    try:
        wire: list[dict[str, Any]] = []
        system_prompt = system or kwargs.get("system") or ""
        if system_prompt:
            wire.append({"role": "system", "content": system_prompt})
        wire.extend(to_openai(messages))

        request_kwargs: dict[str, Any] = {"model": model, "messages": wire}
        if "temperature" in kwargs:
            request_kwargs["temperature"] = kwargs["temperature"]
        cap = token_cap(kwargs)
        if cap:
            request_kwargs["max_completion_tokens"] = cap
        if tools:
            request_kwargs["tools"] = to_openai_tools(tools)
            request_kwargs["tool_choice"] = to_openai_tool_choice(
                tool_choice or ToolChoice(mode="auto")
            )
        if response_format is not None:
            request_kwargs["response_format"] = response_format_kwarg(response_format)

        response = await client.chat.completions.create(**request_kwargs)
        result = result_from_completion(
            response, fallback_prompt=render_as_prompt(messages)
        )
        provider._record_usage(kwargs, result.usage)
        logger.debug(
            "openai_messages_turn",
            extra={
                "model": model,
                "turns": len(messages),
                "tool_calls": len(result.tool_calls),
            },
        )
        return result

    except Exception as e:
        label = getattr(provider, "provider_label", "OpenAI")
        logger.error(f"{label} message generation error: {describe_exception(e)}")
        raise map_provider_exception(e, provider=label) from e
