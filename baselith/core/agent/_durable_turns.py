"""Durable model turns for a checkpointed :meth:`Agent.run`.

Under a checkpoint, the typed agent used to make only its *tool calls*
durable: a run resumed after a crash replayed every recorded observation, but
called the model again for every turn, paying for each one twice and — since a
regenerated turn need not match the original — possibly asking for different
tools than the ones whose results were replayed.

Here every model turn is one checkpoint step. The step key is content
addressed, like a tool call's (:mod:`baselith.core.orchestration.call_keys`):

    ("llm_turn", {"turn": n, "history": H(conversation so far), "spec": H(agent)})

``turn`` is the turn's index in the run (0 for the first model call), and
``history`` a digest of every message sent to the model for that turn — the
caller's history, the prompt, each assistant turn verbatim and each batch of
tool results. ``spec`` digests what else shapes the request (model, system
prompt, tool names, output schema), so an agent redefined between the crash and
the resume misses instead of replaying an answer to a different question.

A resumed pass therefore walks the recorded turns without calling the model:
the first turn's history is the same prompt, its recorded response (tool-call
ids and thinking blocks included, so the next history is byte-identical)
requests the same tools, whose recorded observations replay, which makes the
next turn's history identical again — and so on, until the first turn that was
never recorded. That one runs for real, and everything after it is new.

**Accounting.** A replayed turn does not reach the provider, so it charges no
cost and no tokens to the ambient budget (the run already paid for it, and a
resumed orchestrated run restores those counters from the checkpoint). The
iteration counter still advances: the resumed pass walks the same loop, and
``max_iterations`` bounds turns, not provider calls — exactly as a replayed
tool call still counts against ``max_tool_calls``.

The recorded value is the response as JSON (text, tool calls, stop reason,
usage, the assistant message with its blocks); the provider's raw SDK object is
not persisted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from baselith.core.orchestration.call_keys import canonical_arguments
from baselith.core.services.llm.messages import (
    ContentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from baselith.core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
)
from baselith.core.services.llm.usage import Usage

__all__ = [
    "LLM_TURN_STEP",
    "decode_message",
    "decode_result",
    "durable_turn",
    "encode_message",
    "encode_result",
    "turn_identity",
]

#: Step name of a recorded model turn.
LLM_TURN_STEP = "llm_turn"


def _encode_block(block: ContentBlock) -> dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {
            "type": "tool_use",
            "id": block.id,
            "name": block.name,
            "input": dict(block.input),
        }
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
        }
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "payload": dict(block.payload)}
    return {
        "type": "image",
        "data": block.data,
        "media_type": block.media_type,
        "url": block.url,
    }


def _decode_block(data: dict[str, Any]) -> ContentBlock:
    kind = data.get("type")
    if kind == "text":
        return TextBlock(text=str(data.get("text", "")))
    if kind == "tool_use":
        return ToolUseBlock(
            id=str(data.get("id", "")),
            name=str(data.get("name", "")),
            input=dict(data.get("input") or {}),
        )
    if kind == "tool_result":
        return ToolResultBlock(
            tool_use_id=str(data.get("tool_use_id", "")),
            content=str(data.get("content", "")),
            is_error=bool(data.get("is_error", False)),
        )
    if kind == "thinking":
        return ThinkingBlock(payload=dict(data.get("payload") or {}))
    return ImageBlock(
        data=data.get("data"),
        media_type=str(data.get("media_type", "image/png")),
        url=data.get("url"),
    )


def encode_message(message: Message) -> dict[str, Any]:
    """A JSON-safe copy of ``message``, every block kept (thinking verbatim)."""
    return {
        "role": message.role,
        "content": [_encode_block(block) for block in message.content],
    }


def decode_message(data: dict[str, Any]) -> Message:
    """The :class:`Message` :func:`encode_message` recorded."""
    role: Role = "assistant" if data.get("role") == "assistant" else "user"
    return Message(
        role=role,
        content=[_decode_block(block) for block in data.get("content") or ()],
    )


def encode_result(result: LLMResult, message: Message) -> dict[str, Any]:
    """A JSON-safe record of one model turn.

    Args:
        result: What the provider returned.
        message: The assistant turn appended to the history for it (from
            ``message_from_result``), recorded so a replay appends the very
            same turn — the raw SDK response it may have been built from is
            not persisted.

    Returns:
        The recorded value of the turn's step.
    """
    return {
        "text": result.text,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": dict(call.arguments or {})}
            for call in result.tool_calls
        ],
        "stop_reason": result.stop_reason,
        "tokens_used": result.tokens_used,
        "usage": asdict(result.usage),
        "stop_details": result.stop_details,
        "truncated": result.truncated,
        "native": result.native,
        "message": encode_message(message),
    }


def decode_result(data: dict[str, Any]) -> LLMResult:
    """Rebuild the :class:`LLMResult` a recorded turn stored.

    The rebuilt result carries the recorded assistant ``message``, so
    ``message_from_result`` returns it unchanged and the history the replayed
    pass builds is identical to the one the original pass sent.
    """
    usage = data.get("usage") or {}
    return LLMResult(
        text=data.get("text"),
        tool_calls=[
            ToolCall(
                id=str(call.get("id", "")),
                name=str(call.get("name", "")),
                arguments=dict(call.get("arguments") or {}),
            )
            for call in data.get("tool_calls") or ()
        ],
        stop_reason=data.get("stop_reason"),
        tokens_used=int(data.get("tokens_used", 0)),
        usage=Usage(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cache_read_tokens=int(usage.get("cache_read_tokens", 0)),
            cache_write_tokens=int(usage.get("cache_write_tokens", 0)),
            estimated=bool(usage.get("estimated", False)),
        ),
        stop_details=data.get("stop_details"),
        truncated=bool(data.get("truncated", False)),
        native=bool(data.get("native", True)),
        message=decode_message(data.get("message") or {"role": "assistant"}),
    )


def _digest(value: Any) -> str:
    text = canonical_arguments(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def turn_identity(
    turn: int,
    messages: list[Message],
    *,
    model: str | None,
    system: str | None,
    specs: list[LLMToolSpec] | None,
    response_format: ResponseFormat | None,
) -> dict[str, Any]:
    """The step arguments identifying one model turn (see module docstring).

    Args:
        turn: Index of the turn in the run (0-based).
        messages: The conversation sent for this turn, oldest first.
        model: The agent's model override.
        system: The system prompt sent.
        specs: The tool specs offered.
        response_format: The structured-output schema requested.

    Returns:
        ``{"turn", "history", "spec"}`` — digests only, never the text.
    """
    spec = {
        "model": model,
        "system": system,
        "tools": sorted(s.name for s in specs or ()),
        "schema": response_format.schema if response_format is not None else None,
    }
    return {
        "turn": turn,
        "history": _digest([encode_message(m) for m in messages]),
        "spec": _digest(spec),
    }


async def durable_turn(
    checkpoint: Any,
    identity: dict[str, Any],
    generate: Callable[[], Awaitable[LLMResult]],
    message_for: Callable[[LLMResult], Message],
) -> LLMResult:
    """Run one model turn as a checkpoint step, or replay its record.

    Args:
        checkpoint: The run's manager or scope.
        identity: From :func:`turn_identity`.
        generate: Zero-arg coroutine factory calling the model.
        message_for: Builds the assistant turn for a live result
            (``message_from_result``).

    Returns:
        The turn's result: live, or rebuilt from the record.
    """

    async def _call() -> dict[str, Any]:
        result = await generate()
        return encode_result(result, message_for(result))

    recorded = await checkpoint.run_step(LLM_TURN_STEP, identity, _call, category="llm")
    return decode_result(recorded)
