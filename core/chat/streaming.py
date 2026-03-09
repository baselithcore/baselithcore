"""
Streaming Utilities.

Helper functions for building streaming responses.
Migrated from app/chat/streaming.py
"""

from __future__ import annotations

from typing import Callable, Iterable, Iterator, List, Optional

StreamFunction = Callable[[str], Iterable[str]]
FinalizeFunction = Callable[[str], str]
FinalizeCallback = Callable[[str, str], None]


def build_cached_stream(answer: str) -> Iterator[str]:
    """Build a simple iterator that yields a cached answer."""

    def generator() -> Iterator[str]:
        yield answer

    return generator()


def build_fallback_stream(answer: str) -> Iterator[str]:
    """Build a fallback iterator that yields an answer."""

    def generator() -> Iterator[str]:
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
    Stream an answer from an LLM.

    Args:
        prompt: The prompt to send to the LLM
        stream_fn: Function that yields response chunks
        finalize_fn: Function to post-process the final answer
        on_finalize: Optional callback when answer is complete

    Yields:
        Response chunks from the LLM
    """

    def generator() -> Iterator[str]:
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
    """Compute any suffix added by finalization."""
    final_len = len(final_answer)
    normalized_len = len(normalized_answer)
    if final_len <= normalized_len:
        return ""
    return final_answer[normalized_len:]


__all__ = [
    "build_cached_stream",
    "build_fallback_stream",
    "stream_answer",
    "StreamFunction",
    "FinalizeFunction",
    "FinalizeCallback",
]
