"""Prompt-coercion tool calling for providers without a native tool API.

The tools (and any response schema) are described in an augmented system
prompt, the reply is requested as JSON through the legacy text path, and a
``{"tool": ..., "arguments": {...}}`` / ``{"tool": null, "final": ...}`` object
is parsed back into an :class:`~core.services.llm.tool_calling.LLMResult`.

Three properties the native path has for free are restored here:

* **Failover.** The text call runs through the configured
  ``LLM_FALLBACK_CHAIN`` (:func:`~core.services.llm.fallback_runtime.maybe_run_with_fallback`),
  so a failing primary falls through exactly like plain text generation does.
* **Fail-closed parsing.** Only three wrappers are removed: a leading
  ``<think>…</think>`` block, surrounding whitespace and one enclosing
  markdown code fence. What is left
  must be exactly one JSON object carrying ``tool``/``final``. Nothing is ever
  fished out of prose: a tool-call object the model merely quotes (echoed from
  an untrusted tool output, say) would otherwise be executed.
* **One re-ask.** A reply that is not such an object is answered once with an
  instruction to reply again as JSON. A second failure returns the original
  text with no tool call, so the caller degrades to a plain answer as before —
  never a loop.

Split out of ``structured`` for the module size cap; ``structured`` re-exports
the historical private names.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.errors import LLMRefusalError
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.reasoning_text import THINK_CLOSE, THINK_OPEN
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)
from core.services.llm.usage import Usage

if TYPE_CHECKING:
    from core.services.llm.service import LLMService

__all__ = [
    "REASK_INSTRUCTION",
    "build_fallback_system",
    "generate_fallback",
    "parse_fallback",
    "parse_tool_reply",
]

logger = get_logger(__name__)

#: Appended to the prompt of the single recovery call.
REASK_INSTRUCTION = (
    "Reply again with ONLY a valid JSON object: "
    '{"tool": <tool name>, "arguments": {...}} to call a tool, or '
    '{"tool": null, "final": <your answer as a string>}. '
    "No reasoning, no prose, no markdown."
)

#: How much of the rejected reply is quoted back in the re-ask.
_QUOTE_LIMIT = 2000

#: One markdown code fence enclosing the whole (stripped) reply.
_FENCE = re.compile(
    r"\A```(?:json)?[ \t]*\n(?P<body>.*?)\n?```\Z", re.DOTALL | re.IGNORECASE
)


def _render_tools(tools: list[LLMToolSpec]) -> str:
    """Render tool specs as a compact JSON catalog for the fallback prompt."""
    catalog = [
        {"name": t.name, "description": t.description, "parameters": t.parameters}
        for t in tools
    ]
    return json.dumps(catalog, ensure_ascii=False, sort_keys=True)


def build_fallback_system(
    base_system: str | None,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
) -> str:
    """Augment the system prompt to coerce tool calls / structured JSON.

    Deterministic (sorted keys) so it doesn't defeat prompt caching.
    """
    parts: list[str] = []
    if base_system:
        parts.append(base_system)

    if tools:
        choice = tool_choice or ToolChoice(mode="auto")
        parts.append("You can call tools. Available tools (JSON):")
        parts.append(_render_tools(tools))
        if choice.mode == "tool":
            parts.append(
                f'You MUST call the tool "{choice.name}". Respond with ONLY a '
                'JSON object: {"tool": "' + str(choice.name) + '", "arguments": {...}}.'
            )
        elif choice.mode == "any":
            parts.append(
                "You MUST call one tool. Respond with ONLY a JSON object: "
                '{"tool": <tool name>, "arguments": {...}}.'
            )
        else:
            parts.append(
                "To call a tool, respond with ONLY a JSON object: "
                '{"tool": <tool name>, "arguments": {...}}. '
                'If no tool is needed, respond with {"tool": null, '
                '"final": <your answer as a string>}.'
            )
    elif response_format is not None:
        parts.append(
            "Respond with ONLY a JSON object that conforms to this JSON Schema (JSON):"
        )
        parts.append(
            json.dumps(response_format.schema, ensure_ascii=False, sort_keys=True)
        )

    return "\n\n".join(parts)


def _without_reasoning(content: str) -> str | None:
    """*content* minus a LEADING ``<think>…</think>`` block; ``None`` to refuse.

    Only a block that opens the reply counts: a ``</think>`` planted later (in
    quoted tool output, say) must not open a window onto what follows it.
    Fail-closed in the two ambiguous cases:

    * an unterminated leading ``<think>`` — everything after it is reasoning
      (possibly cut off mid-thought, possibly quoting untrusted content), so
      a tool call in it is never the reply;
    * a reasoning tag left after the block — a second ``</think>`` or a
      ``<think>`` means the boundary cannot be trusted.
    """
    stripped = content.lstrip()
    if not stripped.startswith(THINK_OPEN):
        return content
    rest = stripped[len(THINK_OPEN) :]
    if THINK_CLOSE not in rest:
        return None
    reply = rest.split(THINK_CLOSE, 1)[1]
    if THINK_OPEN in reply or THINK_CLOSE in reply:
        return None
    return reply


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` refusing duplicate keys (a parser differential)."""
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _reply_object(text: str) -> dict[str, Any] | None:
    """*text* as exactly one ``tool``/``final`` JSON object, else ``None``.

    After the reasoning is dropped, only surrounding whitespace and a single
    enclosing code fence are removed; any other surrounding text fails closed.
    """
    without = _without_reasoning(text)
    if without is None:
        return None
    body = without.strip()
    fenced = _FENCE.match(body)
    if fenced is not None:
        body = fenced.group("body").strip()
    try:
        candidate = json.loads(body, object_pairs_hook=_unique_keys)
    except (ValueError, RecursionError):  # JSONDecodeError is a ValueError
        return None
    if isinstance(candidate, dict) and ("tool" in candidate or "final" in candidate):
        return candidate
    return None


def parse_tool_reply(content: str | None) -> LLMResult | None:
    """Parse a coerced tool-turn reply, or ``None`` unless it is one clean object.

    Fail-closed: a leading, closed ``<think>…</think>``, surrounding whitespace
    and one enclosing code fence are the only wrappers removed; an
    unterminated ``<think>`` or a stray reasoning tag after the block refuses. Prose around the object makes the reply ``None``.
    """
    if not content:
        return None
    parsed = _reply_object(content)
    if parsed is None:
        return None

    tool_name = parsed.get("tool")
    if tool_name:
        arguments = parsed.get("arguments")
        return LLMResult(
            text=None,
            tool_calls=[
                ToolCall(
                    id="fallback-call-0",
                    name=str(tool_name),
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            ],
            stop_reason="tool_use",
            native=False,
        )
    final = parsed.get("final")
    return LLMResult(text=str(final) if final is not None else content, native=False)


def parse_fallback(content: str, has_tools: bool) -> LLMResult:
    """Parse a fallback JSON response into an :class:`LLMResult`.

    Tolerant: when the reply is not one clean tool-call object, the raw text is
    returned as ``text`` with no tool calls, so the caller degrades to a plain answer
    rather than erroring.
    """
    if not has_tools:
        # response_format-only (or plain) path: the JSON *is* the answer.
        return LLMResult(text=content or None, native=False)
    parsed = parse_tool_reply(content)
    if parsed is not None:
        return parsed
    return LLMResult(text=content or None, native=False)


def _reask_prompt(prompt: str, rejected: str) -> str:
    """The recovery prompt; the rejected reply is quoted as untrusted data.

    The reply can echo injected text (from a tool output it read), so it goes
    back to the model inside the untrusted envelope, never as bare prompt.
    """
    from core.orchestration.tool_output import wrap_untrusted

    quoted = wrap_untrusted(rejected[:_QUOTE_LIMIT], source="rejected_reply")
    return (
        f"{prompt}\n\nYour previous reply was not a valid JSON object:\n"
        f"{quoted}\n\n{REASK_INSTRUCTION}"
    )


async def _call(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    extra: dict[str, Any],
    sink: list[Usage],
) -> tuple[str, int, str, str]:
    """One text call through the fallback chain (direct when none is set)."""
    from core.services.llm.fallback_runtime import maybe_run_with_fallback

    return await maybe_run_with_fallback(
        service,
        prompt=prompt,
        model=model,
        json_mode=json_mode,
        usage_sink=sink,
        **extra,
    )


async def generate_fallback(
    service: LLMService,
    prompt: str,
    model: str,
    *,
    tools: list[LLMToolSpec] | None,
    tool_choice: ToolChoice | None,
    response_format: ResponseFormat | None,
    system_prompt: str | None,
    temperature: float | None,
    max_tokens: int | None,
    allow_refusal: bool = False,
    usage_sink: list[Usage] | None = None,
) -> tuple[LLMResult, str, str]:
    """Prompt-coercion path for providers without native tool calling.

    ``allow_refusal`` is forwarded as a provider kwarg rather than applied
    here: this path returns through the legacy text API, where the provider
    itself decides whether a refusal raises.

    Returns:
        tuple: ``(LLMResult, serving_provider, serving_model)`` — whichever
        chain stage answered, so the caller bills the model that ran.
    """
    augmented_system = build_fallback_system(
        system_prompt, tools, tool_choice, response_format
    )
    want_json = bool(tools) or response_format is not None

    extra: dict[str, Any] = {}
    if augmented_system:
        extra["system"] = augmented_system
    if temperature is not None:
        extra["temperature"] = temperature
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    if allow_refusal:
        extra["allow_refusal"] = True

    # Owned by the caller when it passed one: a refusal raises out of this
    # function, and the turn still has to be accounted for.
    usage_sink = [] if usage_sink is None else usage_sink
    content, tokens_used, served_by, served_model = await _call(
        service, prompt, model, want_json, extra, usage_sink
    )
    result = parse_tool_reply(content) if tools else None
    if tools and result is None:
        result, tokens_used = await _reask(
            service, prompt, model, want_json, extra, usage_sink, content, tokens_used
        )
    if result is None:
        result = parse_fallback(content, has_tools=bool(tools))
    result.tokens_used = tokens_used
    if usage_sink:
        result.usage = usage_sink[-1]
    return result, served_by, served_model


async def _reask(
    service: LLMService,
    prompt: str,
    model: str,
    json_mode: bool,
    extra: dict[str, Any],
    usage_sink: list[Usage],
    rejected: str,
    tokens_used: int,
) -> tuple[LLMResult | None, int]:
    """The single recovery call; ``(None, tokens)`` when it does not help.

    Its usage is merged into the turn's: both calls ran and both are billed.
    A refusal still raises (with the merged usage booked); any other provider
    failure gives up quietly, leaving the original reply as the answer.
    """
    logger.warning(
        "llm_coerced_reply_unparseable_reasking",
        extra={"model": model, "reply_chars": len(rejected or "")},
    )
    first = usage_sink[-1] if usage_sink else None
    second: list[Usage] = []
    try:
        content, more, _served, _model = await _call(
            service,
            _reask_prompt(prompt, rejected or ""),
            model,
            json_mode,
            extra,
            second,
        )
    except LLMRefusalError:
        _book(usage_sink, first, second)
        raise
    except LLMProviderError as exc:
        logger.warning(
            "llm_coerced_reask_failed", extra={"model": model, "error": str(exc)}
        )
        _book(usage_sink, first, second)
        return None, tokens_used
    _book(usage_sink, first, second)
    return parse_tool_reply(content), tokens_used + more


def _book(usage_sink: list[Usage], first: Usage | None, second: list[Usage]) -> None:
    """Leave the merged usage of both calls as the sink's last record."""
    if not second:
        return
    usage_sink.append(first.merge(second[-1]) if first is not None else second[-1])
