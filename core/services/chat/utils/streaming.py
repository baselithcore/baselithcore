"""
Streaming utilities for chat responses.

Provides functions for building and managing streaming response generators.
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, List, Optional

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
    on_finalize: Optional[FinalizeCallback] = None,
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
        chunks: List[str] = []
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


__all__ = [
    "StreamFunction",
    "FinalizeFunction",
    "FinalizeCallback",
    "build_cached_stream",
    "build_fallback_stream",
    "stream_answer",
]
