"""Wire mappings between neutral messages and provider message shapes.

Pure functions, no SDK import, no client — so the whole conversion surface
tests standalone. Split from :mod:`core.services.llm.messages` (the public
import path, which re-exports everything here) for the file-size cap.

Three asymmetries are worth knowing before reading the code:

* **Anthropic** takes a turn as a list of content blocks, so every tool result
  of a parallel turn belongs in one ``user`` message. **OpenAI** takes one
  ``tool`` message per result, so the same neutral turn expands into several.
* **Anthropic** models ``is_error`` on the ``tool_result`` block. OpenAI has no
  such field, so the flag is rendered into the text instead of being dropped —
  a failure the model cannot see is a failure it will not correct.
* **Thinking blocks** replay verbatim on Anthropic (the signature is validated
  server-side) and are dropped on OpenAI, which has no surface for them.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.services.llm._message_types import (
    ContentBlock,
    ImageBlock,
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.tool_calling import LLMResult

__all__ = [
    "CONVERGENCE_NUDGE",
    "from_anthropic_content",
    "from_openai_message",
    "message_from_result",
    "render_as_prompt",
    "to_anthropic",
    "to_openai",
]

#: Prefix that carries ``is_error`` to providers without an error flag.
_ERROR_PREFIX = "Error: "

#: Appended to a *flattened* conversation once it contains tool results.
#:
#: A model reading a transcript has no ``tool_result`` block telling it the
#: work came back, and without the instruction it keeps re-requesting calls it
#: has already been answered until the iteration cap. Deliberately kept out of
#: :func:`render_as_prompt`, which also feeds the input-token estimate — an
#: instruction is not part of the conversation being measured — so every
#: transcript caller appends it explicitly.
CONVERGENCE_NUDGE = (
    "Continue. Use the tool results above; when you have enough information, "
    "answer without calling more tools."
)


def _anthropic_block(block: ContentBlock) -> dict[str, Any] | None:
    """One neutral block as an Anthropic content block (None to drop it)."""
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
        wire: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
        }
        # Only sent when true: the field is optional and defaults to false, so
        # emitting ``is_error: false`` on every success would change the cached
        # prefix for nothing.
        if block.is_error:
            wire["is_error"] = True
        return wire
    if isinstance(block, ImageBlock):
        if block.data:
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": block.media_type,
                    "data": block.data,
                },
            }
        if block.url:
            return {"type": "image", "source": {"type": "url", "url": block.url}}
        return None
    if isinstance(block, ThinkingBlock):
        # Verbatim: the payload is signed and re-validated on the next turn.
        return dict(block.payload) or None
    return None


def to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
    """Render a neutral history as Anthropic ``messages``.

    Args:
        messages: Conversation so far, oldest first.

    Returns:
        list[dict]: The ``messages`` array for ``client.messages.create``.
        Turns whose every block dropped out are omitted — the API rejects a
        message with empty content.
    """
    wire: list[dict[str, Any]] = []
    for message in messages:
        blocks = [b for b in (_anthropic_block(x) for x in message.content) if b]
        if blocks:
            wire.append({"role": message.role, "content": blocks})
    return wire


def _openai_tool_call(block: ToolUseBlock) -> dict[str, Any]:
    """One neutral tool call as an OpenAI ``tool_calls`` entry."""
    return {
        "id": block.id,
        "type": "function",
        # OpenAI takes the arguments as a JSON *string*; sorted keys keep the
        # rendering stable so a replayed turn hashes identically for caching.
        "function": {
            "name": block.name,
            "arguments": json.dumps(block.input, ensure_ascii=False, sort_keys=True),
        },
    }


def _openai_result_content(block: ToolResultBlock) -> str:
    """Tool-result text for OpenAI, with the error flag folded into it.

    The already-prefixed check is an *exact* match on the marker this function
    writes. Testing for the word "Error" anywhere at the front would let a
    legitimate result — "Errors were handled fine" — suppress the marker, which
    is the failure flag going missing on the one provider that has nowhere else
    to put it.
    """
    if not block.is_error or block.content.startswith(_ERROR_PREFIX):
        return block.content
    return f"{_ERROR_PREFIX}{block.content}"


def _openai_user_content(message: Message) -> list[dict[str, Any]] | str:
    """User content for OpenAI: a plain string unless an image is attached."""
    parts: list[dict[str, Any]] = []
    has_image = False
    for block in message.content:
        if isinstance(block, TextBlock):
            parts.append({"type": "text", "text": block.text})
        elif isinstance(block, ImageBlock):
            has_image = True
            url = (
                f"data:{block.media_type};base64,{block.data}"
                if block.data
                else (block.url or "")
            )
            parts.append({"type": "image_url", "image_url": {"url": url}})
    if not has_image:
        return message.text
    return parts


def to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    """Render a neutral history as OpenAI Chat Completions messages.

    Args:
        messages: Conversation so far, oldest first.

    Returns:
        list[dict]: The ``messages`` array. One neutral user turn carrying
        tool results expands into one ``tool`` message per result, which is the
        only shape OpenAI accepts; anything else that turn carries follows as
        its own ``user`` message.
    """
    wire: list[dict[str, Any]] = []
    for message in messages:
        results = [b for b in message.content if isinstance(b, ToolResultBlock)]
        if results:
            wire.extend(
                {
                    "role": "tool",
                    "tool_call_id": block.tool_use_id,
                    "content": _openai_result_content(block),
                }
                for block in results
            )
            # A neutral turn is a list of blocks, so it may carry tool results
            # *and* a text note or an image — the caller adding an instruction
            # alongside what came back. An OpenAI ``tool`` message has room for
            # neither, and the ``continue`` below used to drop it silently: the
            # model never saw an instruction the caller believed it had sent.
            # It becomes its own user turn, after the results it accompanies,
            # which is where a reader of the transcript expects it.
            extras: list[ContentBlock] = [
                b for b in message.content if not isinstance(b, ToolResultBlock)
            ]
            if extras:
                content = _openai_user_content(Message(role="user", content=extras))
                if content:
                    wire.append({"role": "user", "content": content})
            continue
        if message.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": message.text}
            calls = [_openai_tool_call(b) for b in message.tool_uses]
            if calls:
                entry["tool_calls"] = calls
            wire.append(entry)
            continue
        wire.append({"role": "user", "content": _openai_user_content(message)})
    return wire


def from_anthropic_content(blocks: Any) -> Message:
    """Rebuild the assistant turn from Anthropic content blocks.

    Thinking blocks are captured whole (``type``, ``thinking``/``data``,
    ``signature``) so the next request can replay them unchanged.

    Args:
        blocks: The response's ``content`` list (SDK objects or dicts).

    Returns:
        Message: The assistant turn, ready to append to the history.
    """
    content: list[ContentBlock] = []
    for block in blocks or []:
        kind = _attr(block, "type")
        if kind == "text":
            content.append(TextBlock(text=str(_attr(block, "text") or "")))
        elif kind == "tool_use":
            content.append(
                ToolUseBlock(
                    id=str(_attr(block, "id") or ""),
                    name=str(_attr(block, "name") or ""),
                    input=dict(_attr(block, "input") or {}),
                )
            )
        elif kind in ("thinking", "redacted_thinking"):
            content.append(ThinkingBlock(payload=_thinking_payload(block, kind)))
    return Message(role="assistant", content=content)


def _thinking_payload(block: Any, kind: str) -> dict[str, Any]:
    """The opaque thinking payload, as a plain dict."""
    if isinstance(block, dict):
        return dict(block)
    payload: dict[str, Any] = {"type": kind}
    for name in ("thinking", "data", "signature"):
        value = _attr(block, name)
        if value is not None:
            payload[name] = value
    return payload


def _attr(obj: Any, name: str) -> Any:
    """Read ``name`` off an SDK object or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def from_openai_message(message: Any) -> Message:
    """Rebuild the assistant turn from an OpenAI chat message.

    Args:
        message: ``response.choices[0].message``.

    Returns:
        Message: The assistant turn, with ``tool_calls`` parsed back into
        :class:`ToolUseBlock`s (the arguments JSON string is parsed here).
    """
    content: list[ContentBlock] = []
    text = _attr(message, "content")
    if text:
        content.append(TextBlock(text=str(text)))
    for call in _attr(message, "tool_calls") or []:
        function = _attr(call, "function")
        raw_args = _attr(function, "arguments")
        try:
            arguments = json.loads(raw_args) if raw_args else {}
        except (json.JSONDecodeError, TypeError):
            arguments = {"_raw": raw_args}
        content.append(
            ToolUseBlock(
                id=str(_attr(call, "id") or ""),
                name=str(_attr(function, "name") or ""),
                input=arguments if isinstance(arguments, dict) else {"_raw": arguments},
            )
        )
    return Message(role="assistant", content=content)


def message_from_result(result: LLMResult) -> Message:
    """The assistant turn to append to the history for *result*.

    Preference order matters. A provider that already built the turn wins
    (:attr:`LLMResult.message`), then the raw SDK response — which is the only
    place thinking blocks exist — and only then the flattened
    ``text`` / ``tool_calls`` view, which is all a legacy or fallback path has.

    Args:
        result: What the provider returned.

    Returns:
        Message: The assistant turn.
    """
    built = getattr(result, "message", None)
    if isinstance(built, Message):
        return built
    raw = getattr(result, "raw", None)
    blocks = _attr(raw, "content")
    if isinstance(blocks, list):
        return from_anthropic_content(blocks)
    choices = _attr(raw, "choices")
    if isinstance(choices, list) and choices:
        return from_openai_message(_attr(choices[0], "message"))
    content: list[ContentBlock] = []
    if result.text:
        content.append(TextBlock(text=result.text))
    content.extend(
        ToolUseBlock(id=call.id, name=call.name, input=dict(call.arguments or {}))
        for call in result.tool_calls
    )
    return Message(role="assistant", content=content)


def render_as_prompt(messages: list[Message]) -> str:
    """Flatten a history into one prompt string.

    The compatibility bridge for providers with no message API: the
    conversation still has to reach them, and a labelled transcript is the
    least-lossy shape a single string can hold. Correlation ids are printed so
    a multi-call turn stays readable, but this is a degradation — a provider
    on this path cannot honour ``is_error`` or replay thinking.

    Args:
        messages: Conversation so far, oldest first.

    Returns:
        str: The rendered transcript.
    """
    lines: list[str] = []
    for message in messages:
        for block in message.content:
            if isinstance(block, TextBlock):
                lines.append(f"{message.role}: {block.text}")
            elif isinstance(block, ToolUseBlock):
                lines.append(
                    f"assistant: [tool_use {block.id}] {block.name}"
                    f"({json.dumps(block.input, ensure_ascii=False)})"
                )
            elif isinstance(block, ToolResultBlock):
                label = "tool_error" if block.is_error else "tool_result"
                lines.append(f"user: [{label} {block.tool_use_id}] {block.content}")
            elif isinstance(block, ImageBlock):
                lines.append(f"{message.role}: [image {block.media_type}]")
    return "\n".join(lines)
