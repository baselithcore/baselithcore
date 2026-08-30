"""Document-aware text splitters for the vector store.

Structure-aware complements to :mod:`core.services.vectorstore.chunking`
(which is unchanged): every splitter here conforms to the same
``split_text(text) -> list[str]`` interface used there, and additionally
offers ``split(text) -> list[TextChunk]`` carrying per-chunk metadata
(markdown heading paths, python unit kind/name).

Use :func:`select_splitter` to pick the right splitter for a source file
or MIME type; unknown formats fall back to the existing recursive
character splitting.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from core.observability.logging import get_logger
from core.services.vectorstore.chunking import (
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    chunk_text,
)

logger = get_logger(__name__)

# Max unit size for the code splitter before recursive fallback; larger than
# the prose default so most functions/classes stay whole.
DEFAULT_CODE_CHUNK_SIZE = 2000

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_PYTHON_SUFFIXES = frozenset({".py", ".pyi"})
_MARKDOWN_MIMES = frozenset({"text/markdown", "text/x-markdown"})
_PYTHON_MIMES = frozenset(
    {"text/x-python", "application/x-python-code", "text/x-script.python"}
)


@dataclass(frozen=True)
class TextChunk:
    """A chunk of text plus splitter-provided metadata.

    Attributes:
        text: The chunk content.
        metadata: Splitter-specific context (e.g. ``{"headings": [...]}``
            for markdown, ``{"kind": ..., "name": ...}`` for python).
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SplitterProtocol(Protocol):
    """The splitter interface already used by ``chunking.py``."""

    def split_text(self, text: str) -> list[str]:
        """Split ``text`` into a list of chunk strings."""
        ...


class RecursiveTextSplitter:
    """Adapter exposing the default recursive splitting as a splitter object.

    Delegates to :func:`core.services.vectorstore.chunking.chunk_text`, so
    behavior is identical to the existing pipeline.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        """Initialize with the same defaults as ``chunk_text``.

        Args:
            chunk_size: Maximum chunk size in characters.
            chunk_overlap: Overlap between consecutive chunks.
        """
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split_text(self, text: str) -> list[str]:
        """Split ``text`` recursively into plain string chunks."""
        return chunk_text(text, self._chunk_size, self._chunk_overlap)

    def split(self, text: str) -> list[TextChunk]:
        """Split ``text`` into :class:`TextChunk` objects (empty metadata)."""
        return [TextChunk(text=part) for part in self.split_text(text)]


class MarkdownHeaderSplitter:
    """Split markdown by ATX heading hierarchy (``#``, ``##``, ``###``).

    Each section becomes one chunk whose text starts with its heading line;
    the chunk metadata carries the full heading path, e.g.
    ``{"headings": ["Title", "Section"]}``. Content before the first heading
    is emitted with an empty path. Headings inside fenced code blocks are
    treated as content. Sections longer than ``chunk_size`` fall back to
    recursive splitting, every part keeping the section's heading path.
    Heading-only sections (no body) emit no chunk of their own; their title
    still appears in descendants' paths.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
        max_heading_level: int = 3,
    ) -> None:
        """Initialize the splitter.

        Args:
            chunk_size: Max section size before recursive fallback.
            chunk_overlap: Overlap used by the recursive fallback.
            max_heading_level: Deepest heading level that starts a new
                section; deeper headings are kept as section content.
        """
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._max_heading_level = max_heading_level

    def split(self, text: str) -> list[TextChunk]:
        """Split markdown ``text`` into heading-scoped chunks."""
        if not text or not text.strip():
            return []

        chunks: list[TextChunk] = []
        headings: list[str] = []
        section_lines: list[str] = []
        has_heading = False  # current section starts with a heading line
        in_fence = False

        def flush() -> None:
            body_lines = section_lines[1:] if has_heading else section_lines
            if not any(line.strip() for line in body_lines):
                return
            section_text = "\n".join(section_lines).strip()
            self._emit(section_text, headings, chunks)

        for line in text.splitlines():
            if _FENCE_RE.match(line):
                in_fence = not in_fence
                section_lines.append(line)
                continue
            match = None if in_fence else _HEADING_RE.match(line)
            if match and len(match.group(1)) <= self._max_heading_level:
                flush()
                level = len(match.group(1))
                headings = headings[: level - 1] + [match.group(2)]
                section_lines = [line]
                has_heading = True
            else:
                section_lines.append(line)
        flush()
        return chunks

    def split_text(self, text: str) -> list[str]:
        """Split markdown ``text`` into plain string chunks."""
        return [chunk.text for chunk in self.split(text)]

    def _emit(
        self, section_text: str, headings: list[str], chunks: list[TextChunk]
    ) -> None:
        """Append ``section_text`` as one chunk, or several if oversized."""
        if len(section_text) <= self._chunk_size:
            parts = [section_text]
        else:
            parts = chunk_text(section_text, self._chunk_size, self._chunk_overlap)
        chunks.extend(
            TextChunk(text=part, metadata={"headings": list(headings)})
            for part in parts
        )


class PythonCodeSplitter:
    """AST-aware splitter for Python source (stdlib ``ast``).

    Splits a module into top-level function/class units — decorators and
    docstrings included — with module-level code (preamble, code between
    or after units) emitted as its own ``kind="module"`` chunks. Unit
    chunks carry ``{"kind": "function"|"class", "name": ...}``. Units
    longer than ``chunk_size`` fall back to recursive splitting keeping
    their metadata; input that fails to parse falls back entirely to
    recursive splitting with ``{"kind": "fallback"}``.
    """

    def __init__(
        self,
        chunk_size: int = DEFAULT_CODE_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ) -> None:
        """Initialize the splitter.

        Args:
            chunk_size: Max unit size before recursive fallback.
            chunk_overlap: Overlap used by the recursive fallback.
        """
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def split(self, text: str) -> list[TextChunk]:
        """Split python ``text`` into AST-scoped chunks."""
        if not text or not text.strip():
            return []
        try:
            tree = ast.parse(text)
        except SyntaxError:
            logger.debug("PythonCodeSplitter: unparsable source, recursive fallback")
            return [
                TextChunk(text=part, metadata={"kind": "fallback"})
                for part in chunk_text(text, self._chunk_size, self._chunk_overlap)
            ]

        lines = text.splitlines()
        chunks: list[TextChunk] = []
        cursor = 1  # 1-based first line not yet consumed
        for start, end, kind, name in self._top_level_units(tree):
            self._emit_module_segment(lines, cursor, start - 1, chunks)
            unit_text = "\n".join(lines[start - 1 : end]).strip("\n")
            self._emit(unit_text, {"kind": kind, "name": name}, chunks)
            cursor = end + 1
        self._emit_module_segment(lines, cursor, len(lines), chunks)
        return chunks

    def split_text(self, text: str) -> list[str]:
        """Split python ``text`` into plain string chunks."""
        return [chunk.text for chunk in self.split(text)]

    @staticmethod
    def _top_level_units(tree: ast.Module) -> list[tuple[int, int, str, str]]:
        """Return (start_line, end_line, kind, name) for top-level defs."""
        units: list[tuple[int, int, str, str]] = []
        for node in tree.body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                start = node.lineno
                if node.decorator_list:
                    start = min(start, *(d.lineno for d in node.decorator_list))
                end = node.end_lineno or start
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                units.append((start, end, kind, node.name))
        units.sort()
        return units

    def _emit_module_segment(
        self, lines: list[str], first: int, last: int, chunks: list[TextChunk]
    ) -> None:
        """Emit lines ``first..last`` (1-based, inclusive) as module code."""
        if first > last:
            return
        segment = "\n".join(lines[first - 1 : last]).strip("\n")
        if not segment.strip():
            return
        self._emit(segment, {"kind": "module", "name": None}, chunks)

    def _emit(
        self, text: str, metadata: dict[str, Any], chunks: list[TextChunk]
    ) -> None:
        """Append ``text`` as one chunk, or several if oversized."""
        if len(text) <= self._chunk_size:
            parts = [text]
        else:
            parts = chunk_text(text, self._chunk_size, self._chunk_overlap)
        chunks.extend(TextChunk(text=part, metadata=dict(metadata)) for part in parts)


def select_splitter(
    source: str | Path | None, mime: str | None = None
) -> MarkdownHeaderSplitter | PythonCodeSplitter | RecursiveTextSplitter:
    """Pick the right splitter for a source path and/or MIME type.

    Args:
        source: File path or name of the document (may be ``None``).
        mime: Optional MIME type, consulted when the extension is not
            conclusive.

    Returns:
        A splitter conforming to :class:`SplitterProtocol` —
        :class:`MarkdownHeaderSplitter` for markdown,
        :class:`PythonCodeSplitter` for python source, and the default
        :class:`RecursiveTextSplitter` for everything else.
    """
    suffix = Path(source).suffix.lower() if source is not None else ""
    mime_normalized = (mime or "").lower().split(";", 1)[0].strip()

    if suffix in _MARKDOWN_SUFFIXES or mime_normalized in _MARKDOWN_MIMES:
        return MarkdownHeaderSplitter()
    if suffix in _PYTHON_SUFFIXES or mime_normalized in _PYTHON_MIMES:
        return PythonCodeSplitter()
    return RecursiveTextSplitter()


__all__ = [
    "DEFAULT_CODE_CHUNK_SIZE",
    "MarkdownHeaderSplitter",
    "PythonCodeSplitter",
    "RecursiveTextSplitter",
    "SplitterProtocol",
    "TextChunk",
    "select_splitter",
]
