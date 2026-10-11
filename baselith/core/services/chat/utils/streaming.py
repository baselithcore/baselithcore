"""
Streaming utilities for chat responses.

Provides functions for building and managing streaming response generators.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from typing import Any

from baselith.core.observability.logging import get_logger

logger = get_logger(__name__)

StreamFunction = Callable[[str], Iterable[str]]
FinalizeFunction = Callable[[str], str]
FinalizeCallback = Callable[[str, str], None]


def build_cached_stream(answer: str) -> Iterator[str]:
    """Build a single-chunk stream from a cached answer."""

    def generator() -> Iterator[str]:
        """Generator that yields the cached answer once."""
        yield answer

    return generator()


def build_fallback_stream(answer: str) -> Iterator[str]:
    """Build a fallback stream for error cases."""

    def generator() -> Iterator[str]:
        """Generator that yields the fallback answer once."""
        yield answer

    return generator()


def stream_answer(
    prompt: str,
    *,
    stream_fn: StreamFunction,
    finalize_fn: FinalizeFunction,
    on_finalize: FinalizeCallback | None = None,
) -> Iterator[str]:
    """
    Stream answer chunks from an LLM, with optional finalization.

    Args:
        prompt: The prompt to send to the LLM.
        stream_fn: Function that yields response chunks.
        finalize_fn: Function to post-process the final answer.
        on_finalize: Optional callback when streaming completes.

    Yields:
        Response chunks as they are generated.
    """

    def generator() -> Iterator[str]:
        """Generator that streams answer chunks from the LLM piece by piece."""
        chunks: list[str] = []
        for piece in stream_fn(prompt):
            if not piece:
                continue
            chunks.append(piece)
            yield piece

        raw_answer = "".join(chunks)
        normalized_answer = raw_answer.strip()
        final_answer = finalize_fn(normalized_answer)
        if on_finalize is not None:
            on_finalize(final_answer, normalized_answer)

        extra_suffix = _compute_suffix(final_answer, normalized_answer)
        if extra_suffix:
            yield extra_suffix

    return generator()


def _compute_suffix(final_answer: str, normalized_answer: str) -> str:
    """
    Calculate the difference between finalized and original answer.

    Used to determine if any extra tokens were added during post-processing
    that need to be yielded to the stream.

    Args:
        final_answer: The post-processed text.
        normalized_answer: The original stripped text.

    Returns:
        str: The extra suffix to yield.
    """
    final_len = len(final_answer)
    normalized_len = len(normalized_answer)
    if final_len <= normalized_len:
        return ""
    return final_answer[normalized_len:]


def on_stream_end(
    stream: AsyncIterator[str], callback: Callable[[], None]
) -> AsyncIterator[str]:
    """Wrap ``stream`` so ``callback`` runs once it is drained, fails or closes.

    Args:
        stream: The async chunk stream to pass through unchanged.
        callback: Invoked exactly once when the wrapper finishes, for any
            reason (exhaustion, an exception, or the consumer closing it).

    Returns:
        An async iterator yielding the same chunks as ``stream``; closing it
        closes ``stream`` too (an async generator's close never reaches the
        one it iterates, and a durable run behind it records its interruption
        and releases its lease on that close).
    """

    async def wrapped() -> AsyncIterator[str]:
        try:
            async for chunk in stream:
                yield chunk
        finally:
            try:
                await aclose_quietly(stream)
            finally:
                callback()

    return wrapped()


async def aclose_quietly(stream: Any) -> None:
    """Close ``stream`` when it can be closed; a failing close is logged only."""
    aclose = getattr(stream, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:
        logger.debug("chat_stream_source_close_failed error=%s", exc)


__all__ = [
    "FinalizeCallback",
    "aclose_quietly",
    "FinalizeFunction",
    "StreamFunction",
    "build_cached_stream",
    "build_fallback_stream",
    "on_stream_end",
    "stream_answer",
]
