"""Pure request/response mappings shared by the OpenAI provider's paths.

``_openai_request`` shapes the *kwargs* of a call; this module shapes the
*payload* — tool definitions, tool choice, the structured-output constraint —
and reads a completion back into the neutral :class:`LLMResult`. Three call
sites need exactly the same answers (``generate_structured``,
``generate_messages`` and anything added next), and a second copy of "what does
``finish_reason`` mean" is how two paths start disagreeing about a refusal.

Split from ``openai_provider`` for the file-size cap; no client, no SDK import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.services.llm._strict_schema import to_strict_schema
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.messages import from_openai_message
from core.services.llm.providers._openai_request import extract_usage, refusal_of
from core.services.llm.stop_reasons import STOP_REFUSAL
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.usage import Usage

__all__ = [
    "response_format_kwarg",
    "result_from_completion",
    "to_openai_tool_choice",
    "to_openai_tools",
]


def to_openai_tools(tools: list[LLMToolSpec]) -> list[dict[str, Any]]:
    """Map neutral tool specs to OpenAI ``tools`` (function) entries."""
    result: list[dict[str, Any]] = []
    for spec in tools:
        function: dict[str, Any] = {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters or {"type": "object"},
        }
        if spec.strict:
            function["strict"] = True
        result.append({"type": "function", "function": function})
    return result


def to_openai_tool_choice(choice: ToolChoice) -> Any:
    """Map a neutral :class:`ToolChoice` to OpenAI's ``tool_choice`` value."""
    if choice.mode == "tool":
        return {"type": "function", "function": {"name": choice.name}}
    if choice.mode == "any":
        return "required"
    # "auto" | "none" map to the string forms.
    return choice.mode


def response_format_kwarg(response_format: ResponseFormat) -> dict[str, Any]:
    """The ``response_format`` request field for a structured-output request.

    Strict enforcement accepts a narrower dialect than JSON Schema: every
    property must be required and no object may allow extras. A Pydantic model
    with a defaulted field violates that and is rejected with a 400 before
    generation, so the schema is adapted here rather than at every caller.
    """
    schema = (
        to_strict_schema(response_format.schema)
        if response_format.strict
        else response_format.schema
    )
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_format.name,
            "schema": schema,
            "strict": response_format.strict,
        },
    }


def _tool_calls_of(message: Any) -> list[ToolCall]:
    """Parsed tool calls of a chat message (arguments decoded, never re-parsed)."""
    turn = from_openai_message(message)
    return [
        ToolCall(id=use.id, name=use.name, arguments=dict(use.input))
        for use in turn.tool_uses
    ]


def result_from_completion(response: Any, *, fallback_prompt: str) -> LLMResult:
    """Read a chat completion into the neutral :class:`LLMResult`.

    Args:
        response: The chat completion.
        fallback_prompt: Prompt text used only to estimate tokens when the
            server reported no usage at all.

    Returns:
        LLMResult: text and/or tool calls, the metered usage split, the
        normalised stop reason, and ``message`` — the assistant turn as neutral
        content blocks, ready to append to a message history.
    """
    choice = response.choices[0]
    message = choice.message
    raw_content = getattr(message, "content", None)
    text = raw_content.strip() if raw_content else None

    usage: Usage
    usage, reported_total = extract_usage(response)
    tokens_used = (
        usage.total
        or reported_total
        or (estimate_tokens(fallback_prompt) + estimate_tokens(text or ""))
    )

    # OpenAI reports a refusal on the message, not as a finish reason;
    # normalise it so callers branch on one vocabulary.
    refusal = refusal_of(message)
    stop_reason = getattr(choice, "finish_reason", None)
    return LLMResult(
        text=text,
        tool_calls=_tool_calls_of(message),
        stop_reason=STOP_REFUSAL if refusal else stop_reason,
        stop_details={"explanation": refusal} if refusal else None,
        tokens_used=tokens_used,
        usage=usage,
        native=True,
        raw=response,
        message=from_openai_message(message),
    )
