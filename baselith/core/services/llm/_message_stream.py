"""One streamed model turn for the message API (agent text, token by token).

:func:`stream_attempt` is the streaming sibling of the buffered provider call in
:mod:`baselith.core.services.llm.message_runtime`: same request, same consolidated
:class:`~baselith.core.services.llm.tool_calling.LLMResult` at the end, but every text
chunk is handed to ``on_text`` the moment the provider produces it.

Failure semantics — the one rule that matters:

* A failure **before the first text chunk** is invisible to the caller: it
  retries and fails over the fallback chain exactly like a buffered call.
* A failure **after** text reached the caller is *committed*. Restarting would
  duplicate or contradict what was already rendered, so it surfaces as
  :class:`~baselith.core.services.llm.exceptions.StreamInterruptedError` (never retried,
  never failed over) carrying the partial text.
* Cancellation closes the provider stream (and the HTTP connection under it)
  before it propagates; nothing is left running.

Tool calls are never streamed: they arrive whole on the final result.
"""

from __future__ import annotations

from contextlib import aclosing
from typing import TYPE_CHECKING, Any, cast

from baselith.core.services.llm._deadline import stream_within_deadline
from baselith.core.services.llm._fallback_support import fatal_exception_types
from baselith.core.services.llm.errors import safe_error_text
from baselith.core.services.llm.exceptions import (
    LLMProviderError,
    StreamInterruptedError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import AsyncGenerator, Callable

    from baselith.core.services.llm.messages import Message
    from baselith.core.services.llm.service import LLMService
    from baselith.core.services.llm.tool_calling import LLMResult

__all__ = ["can_stream_turn", "stream_attempt"]


def can_stream_turn(service: LLMService, kwargs: dict[str, Any]) -> bool:
    """Whether this turn may go over the provider's streaming message API.

    All of: the kill switch (``LLM_STREAM_AGENT_TEXT``) is on, the provider
    declares ``supports_streaming_tools is True`` and has the method, and the
    turn is plain text — a structured-output request (``response_format``) needs
    the whole answer to validate, so it stays buffered.

    Args:
        service: The service (or fallback clone) that will serve the turn.
        kwargs: The turn's provider kwargs.
    """
    if getattr(service.config, "stream_agent_text", True) is not True:
        return False
    provider = service.provider
    if getattr(provider, "supports_streaming_tools", False) is not True:
        return False
    if not callable(getattr(provider, "generate_messages_stream", None)):
        return False
    return kwargs.get("response_format") is None


async def stream_attempt(
    service: LLMService,
    messages: list[Message],
    model: str,
    on_text: Callable[[str], None],
    **kwargs: Any,
) -> LLMResult:
    """Run one streamed turn, calling ``on_text`` per chunk.

    Args:
        service: The service whose provider serves the turn.
        messages: The conversation so far.
        model: Resolved model name.
        on_text: Receives each non-empty text chunk, in order.
        **kwargs: Provider kwargs (``tools``, ``system``, ``tool_choice`` ...).

    Returns:
        The consolidated turn, identical in shape to a buffered one.

    Raises:
        StreamInterruptedError: The stream broke after text was delivered.
        LLMProviderError: The stream ended without a final result.
    """
    from baselith.core.services.llm.stream_events import StreamEnd, TextDelta

    emitted: list[str] = []
    final: LLMResult | None = None
    inner = cast(
        "AsyncGenerator[Any, None]",
        service.provider.generate_messages_stream(  # type: ignore[attr-defined]
            messages, model, **kwargs
        ),
    )
    outer = cast("AsyncGenerator[Any, None]", stream_within_deadline(inner))
    try:
        # Both generators are closed explicitly: abandoning the wrapper does
        # not close the provider stream under it, and an unclosed stream is a
        # leaked HTTP connection.
        async with aclosing(inner), aclosing(outer) as events:
            async for event in events:
                if isinstance(event, StreamEnd):
                    final = event.result
                elif isinstance(event, TextDelta) and event.text:
                    emitted.append(event.text)
                    on_text(event.text)
    except Exception as exc:
        if not emitted or isinstance(exc, fatal_exception_types()):
            raise
        raise StreamInterruptedError(
            f"stream interrupted after {sum(map(len, emitted))} characters: "
            f"{safe_error_text(exc)}",
            partial_text="".join(emitted),
        ) from None
    if final is None:
        raise LLMProviderError("provider stream ended without a final result")
    return final
