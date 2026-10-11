"""Recursive character text splitter, without the LangChain dependency.

Chunking needed exactly one algorithm — ``RecursiveCharacterTextSplitter`` — and
paid for it with ``langchain-text-splitters`` → ``langchain-core`` →
``langsmith``, a subtree large enough that the project already carries a
security constraint for it (``langsmith>=0.8.18``, GHSA-f4xh-w4cj-qxq8:
arbitrary server-side file read). This module is that algorithm, and nothing
else.

**Byte-for-byte compatible on purpose.** Chunk boundaries are not cosmetic: a
shift re-chunks every indexed corpus, so an existing vector store would hold
chunks that no longer correspond to anything the splitter produces.
``tests/unit/core/services/vectorstore/test_recursive_splitter.py`` pins the
output against results captured from the LangChain implementation across a
matrix of texts and ``(chunk_size, chunk_overlap)`` pairs; a divergence is a
test failure, not a silent quality regression.

The behaviour reproduced is the configuration this project used: default
``keep_separator=True`` (the separator is prepended to the piece that follows),
``strip_whitespace=True``, ``length_function=len``, non-regex separators.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence

__all__ = ["RecursiveCharacterSplitter"]


def _split_keeping_separator(text: str, separator: str) -> list[str]:
    """Split on ``separator``, prepending it to the piece that follows.

    Mirrors LangChain's ``_split_text_with_regex`` with ``keep_separator=True``.
    An empty separator degrades to a character-wise split, which is what makes
    the final ``""`` separator a guaranteed terminator for the recursion.
    """
    if not separator:
        return [character for character in text if character]

    parts = re.split(f"({re.escape(separator)})", text)
    merged = [parts[index] + parts[index + 1] for index in range(1, len(parts), 2)]
    if len(parts) % 2 == 0:
        merged += parts[-1:]
    merged = [parts[0], *merged]
    return [piece for piece in merged if piece]


class RecursiveCharacterSplitter:
    """Split text on the first separator that appears, recursing on long pieces.

    Args:
        chunk_size: Maximum characters per chunk (soft: a single piece with no
            usable separator is emitted whole rather than cut mid-token).
        chunk_overlap: Characters of overlap carried between adjacent chunks.
        separators: Tried in order; the last one should be ``""`` so the
            recursion always terminates.
        length_function: How a piece's length is measured.
    """

    def __init__(
        self,
        chunk_size: int,
        chunk_overlap: int,
        separators: Sequence[str],
        length_function: Callable[[str], int] = len,
    ) -> None:
        if chunk_overlap > chunk_size:
            raise ValueError(
                f"chunk_overlap ({chunk_overlap}) must not exceed "
                f"chunk_size ({chunk_size})"
            )
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._separators = list(separators)
        self._length = length_function

    def split_text(self, text: str) -> list[str]:
        """Return ``text`` split into chunks."""
        return self._split(text, self._separators)

    # -- internals ---------------------------------------------------------

    def _split(self, text: str, separators: list[str]) -> list[str]:
        final: list[str] = []
        separator = separators[-1] if separators else ""
        remaining: list[str] = []
        for index, candidate in enumerate(separators):
            if not candidate:
                separator = candidate
                break
            if re.search(re.escape(candidate), text):
                separator = candidate
                remaining = separators[index + 1 :]
                break

        pieces = _split_keeping_separator(text, separator)

        # keep_separator=True: the separator already rides on each piece, so
        # merging joins with the empty string.
        pending: list[str] = []
        for piece in pieces:
            if self._length(piece) < self._chunk_size:
                pending.append(piece)
                continue
            if pending:
                final.extend(self._merge(pending))
                pending = []
            if not remaining:
                final.append(piece)
            else:
                final.extend(self._split(piece, remaining))
        if pending:
            final.extend(self._merge(pending))
        return final

    def _merge(self, pieces: Iterable[str]) -> list[str]:
        """Pack pieces into chunks of at most ``chunk_size``, with overlap."""
        chunks: list[str] = []
        current: list[str] = []
        total = 0

        for piece in pieces:
            length = self._length(piece)
            if total + length > self._chunk_size and current:
                joined = "".join(current).strip()
                if joined:
                    chunks.append(joined)
                # Drop from the front until the carried-over tail fits both the
                # overlap budget and the incoming piece.
                while total > self._chunk_overlap or (
                    total + length > self._chunk_size and total > 0
                ):
                    total -= self._length(current[0])
                    current = current[1:]
                    if not current:
                        break
            current.append(piece)
            total += length

        joined = "".join(current).strip()
        if joined:
            chunks.append(joined)
        return chunks
