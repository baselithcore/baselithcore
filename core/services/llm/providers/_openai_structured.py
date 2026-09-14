"""Native tool-calling / structured-output call for the OpenAI provider.

Split out of ``openai_provider`` for the module size cap, the same way the
Anthropic provider keeps its structured, streaming and message bodies in
siblings. Takes the provider instance as its first argument and reuses its
client and usage sink, so behaviour is identical to the method that delegates
here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.errors import map_provider_exception
from core.services.llm.exceptions import describe_exception
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

__all__ = ["generate_structured"]


async def generate_structured(
    provider: OpenAIProvider,
    prompt: str,
    model: str,
    *,
    tools: list[LLMToolSpec] | None = None,
    tool_choice: ToolChoice | None = None,
    response_format: ResponseFormat | None = None,
    **kwargs: Any,
) -> LLMResult:
    """Generate using OpenAI's native function-calling / structured outputs.

    Args:
        provider: The owning :class:`OpenAIProvider`.
        prompt: User turn.
        model: Model name.
        tools: Tools the model may call.
        tool_choice: Selection policy (defaults to auto when tools present).
        response_format: Optional structured-output constraint.
        **kwargs: ``system``, ``temperature``, ``max_tokens``.

    Returns:
        LLMResult: text and/or structured tool calls with token usage.
    """
    client = provider._ensure_client()
    try:
        messages: list[dict[str, Any]] = []
        system_prompt = kwargs.get("system", "")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        request_kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if "temperature" in kwargs:
            request_kwargs["temperature"] = kwargs["temperature"]
        # Chat Completions moved to max_completion_tokens; the reasoning
        # models reject the old name outright.
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
        result = result_from_completion(response, fallback_prompt=prompt)
        provider._record_usage(kwargs, result.usage)
        return result

    except Exception as e:
        logger.error(f"OpenAI structured generation error: {describe_exception(e)}")
        raise map_provider_exception(e, provider="OpenAI") from e
