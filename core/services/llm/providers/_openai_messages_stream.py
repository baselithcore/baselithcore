"""Streamed message-API turn for the OpenAI-compatible providers.

The buffered turn (:mod:`._openai_messages`) waits for the whole completion;
this one reads the same request as Server-Sent Events and hands every text
chunk to the caller the moment it arrives, which is what lets a chat surface
write an answer out token by token instead of all at once at the end.

What stays identical to the buffered turn, by construction:

* the request (:func:`._openai_messages.build_request`);
* the result: the accumulated message is read back through
  :func:`._openai_mapping.result_from_completion`, so tool calls, usage split,
  stop reason and the replayable assistant turn come out the same.

What is specific to a stream:

* **Tool calls are never streamed.** Their ``arguments`` arrive as partial JSON
  fragments; they are accumulated per ``index`` and surface only on the final
  :class:`~core.services.llm.stream_events.StreamEnd`, parsed.
* **Reasoning never becomes text.** A server that parses reasoning sends it in
  ``reasoning_content`` / ``reasoning``; one that does not leaves it inline,
  closed by ``</think>`` — the optional ``guard`` (a
  :class:`~core.services.llm.reasoning_text.ReasoningStreamFilter`) drops it
  across chunk boundaries.
* **Usage** comes from the terminal usage chunk (``include_usage``); a server
  that sends none gets the same estimate the buffered path falls back to.
* **Leading blank space is held** until the first visible character, so a
  stream never opens on a run of newlines the buffered result would strip.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.services.llm.errors import map_provider_exception, safe_error_text
from core.services.llm.messages import Message, render_as_prompt
from core.services.llm.providers._openai_mapping import result_from_completion
from core.services.llm.providers._openai_messages import build_request
from core.services.llm.tool_calling import LLMToolSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.services.llm.reasoning_text import ReasoningStreamFilter
    from core.services.llm.stream_events import StreamEvent

logger = get_logger(__name__)

__all__ = ["stream_messages"]


def _reasoning_of(delta: Any) -> str | None:
    """The reasoning a parsing server streams in its own delta field."""
    for name in ("reasoning_content", "reasoning"):
        value = getattr(delta, name, None)
        if isinstance(value, str) and value:
            return value
    return None


async def _close(stream: Any) -> None:
    """Release the HTTP stream (idempotent; a dead stream may refuse)."""
    for name in ("close", "aclose"):
        closer = getattr(stream, name, None)
        if closer is None:
            continue
        try:
            await closer()
        except Exception as exc:  # closing an already-dead stream may fail
            logger.debug("openai_stream_close_failed", extra={"error": str(exc)})
        return


class _ToolCallAccumulator:
    """Assembles streamed ``tool_calls`` fragments by their ``index``."""

    def __init__(self) -> None:
        self._calls: dict[int, dict[str, str]] = {}

    def feed(self, fragments: Any) -> None:
        """Merge one delta's ``tool_calls`` fragments."""
        for position, fragment in enumerate(fragments or ()):
            index = getattr(fragment, "index", None)
            if not isinstance(index, int):
                index = position
            entry = self._calls.setdefault(index, {"id": "", "name": "", "args": ""})
            call_id = getattr(fragment, "id", None)
            if isinstance(call_id, str) and call_id:
                entry["id"] = call_id
            function = getattr(fragment, "function", None)
            name = getattr(function, "name", None)
            if isinstance(name, str) and name:
                entry["name"] = name
            args = getattr(function, "arguments", None)
            if isinstance(args, str):
                entry["args"] += args

    def build(self) -> list[Any]:
        """The calls as SDK-shaped objects, in index order, ids guaranteed."""
        return [
            SimpleNamespace(
                id=entry["id"] or f"call_{index}",
                type="function",
                function=SimpleNamespace(name=entry["name"], arguments=entry["args"]),
            )
            for index, entry in sorted(self._calls.items())
            if entry["name"]
        ]


async def stream_messages(
    client: Any,
    messages: list[Message],
    model: str,
    *,
    tools: list[LLMToolSpec] | None = None,
    system: str | None = None,
    guard: ReasoningStreamFilter | None = None,
    **kwargs: Any,
) -> AsyncIterator[StreamEvent]:
    """Stream one Chat Completions turn from a neutral message history.

    Args:
        client: The SDK client the request goes through (a routed one for
            vLLM, the provider's own otherwise).
        messages: Conversation so far, oldest first.
        model: Model name.
        tools: Tools the model may call (their arguments are not streamed).
        system: System prompt.
        guard: Drops inline reasoning from the text, across chunk boundaries.
        **kwargs: The same surface as the buffered turn (``tool_choice``,
            ``temperature``, ``max_tokens``, ``usage_sink``).

    Yields:
        :class:`~core.services.llm.stream_events.TextDelta` per visible text
        chunk, then exactly one ``StreamEnd`` carrying the authoritative
        :class:`~core.services.llm.tool_calling.LLMResult`.
    """
    # Lazy: stream_events imports the service layer (import cycle).
    from core.services.llm.stream_events import StreamEnd, TextDelta

    request = build_request(messages, model, tools=tools, system=system, kwargs=kwargs)
    pieces: list[str] = []
    refusal: list[str] = []
    calls = _ToolCallAccumulator()
    finish_reason: str | None = None
    usage: Any = None
    started = False  # a non-blank character has been emitted

    def _emit(text: str) -> str:
        nonlocal started
        if not started:
            text = text.lstrip()
            started = bool(text)
        if text:
            pieces.append(text)
        return text

    stream: Any = None
    try:
        stream = await client.chat.completions.create(
            **request, stream=True, stream_options={"include_usage": True}
        )
        async for chunk in stream:
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
            if not getattr(chunk, "choices", None):
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason
            calls.feed(getattr(delta, "tool_calls", None))
            piece = getattr(delta, "refusal", None)
            if isinstance(piece, str) and piece:
                refusal.append(piece)
            content = str(getattr(delta, "content", None) or "")
            if guard is not None:
                # A parsing server sends the answer clean: stop holding it.
                held = guard.passthrough() if _reasoning_of(delta) else ""
                content = held + guard.feed(content)
            text = _emit(content) if content else ""
            if text:
                yield TextDelta(text)
        if guard is not None:
            tail = _emit(guard.finish())
            if tail:
                yield TextDelta(tail)
    except Exception as e:
        # Type and status only: the SDK's message carries the response body,
        # which a server may fill with the prompt or a credential it echoed.
        logger.error(f"streaming message error: {safe_error_text(e)}")
        raise map_provider_exception(
            e, provider="OpenAI-compatible", action="streaming", redact=True
        ) from None
    finally:
        if stream is not None:
            await _close(stream)

    message = SimpleNamespace(
        content="".join(pieces) or None,
        tool_calls=calls.build(),
        refusal="".join(refusal) or None,
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=usage,
    )
    result = result_from_completion(
        completion, fallback_prompt=render_as_prompt(messages)
    )
    provider_sink = kwargs.get("usage_sink")
    if isinstance(provider_sink, list):
        provider_sink.append(result.usage)
    yield StreamEnd(result)
