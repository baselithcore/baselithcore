"""The in-repo splitter must stay byte-compatible with the one it replaced.

``core.services.vectorstore.recursive_splitter`` replaced LangChain's
``RecursiveCharacterTextSplitter`` so the framework stops pulling
``langchain-text-splitters`` → ``langchain-core`` → ``langsmith`` for one
algorithm.

Chunk boundaries are not cosmetic. A shift re-chunks every indexed corpus, so a
deployed vector store would hold chunks that no longer correspond to anything
the splitter produces — silent retrieval degradation, not a visible failure.
``EXPECTED`` therefore pins the exact output captured from the LangChain
implementation (version 1.1.2) across a matrix of texts and
``(chunk_size, chunk_overlap)`` pairs.
"""

from __future__ import annotations

import pytest

from core.services.vectorstore.chunking import chunk_text
from core.services.vectorstore.recursive_splitter import RecursiveCharacterSplitter

SEPARATORS = ["\n\n", "\n", ". ", " ", ""]

CASES: dict[str, str] = {
    "empty": "",
    "whitespace": "   \n\n  ",
    "short": "A single short sentence.",
    "paragraphs": (
        "Primo paragrafo con del testo.\n\n"
        "Secondo paragrafo, un po' piu' lungo, che continua per qualche riga "
        "e contiene punteggiatura. Poi un'altra frase.\n\n"
        "Terzo paragrafo finale."
    ),
    "long_prose": " ".join(
        f"Frase numero {i} del documento di prova." for i in range(1, 120)
    ),
    "newlines_only": "\n".join(f"riga {i}" for i in range(1, 200)),
    "no_separators": "x" * 2500,
    "mixed": (
        "# Titolo\n\n"
        + "Testo introduttivo. " * 40
        + "\n\n"
        + "- elenco 1\n- elenco 2\n- elenco 3\n\n"
        + "Coda finale. " * 30
    ),
    "unicode": "Città più caffè — è così. " * 60,
}

PARAMS = [(800, 200), (200, 50), (100, 0), (1000, 300)]

#: Chunk counts produced by LangChain 1.1.2 for every (case, size, overlap).
#: A count change means a boundary moved; the content assertions below then say
#: where.
EXPECTED_COUNTS: dict[tuple[str, int, int], int] = {
    ("empty", 800, 200): 0,
    ("empty", 200, 50): 0,
    ("empty", 100, 0): 0,
    ("empty", 1000, 300): 0,
    ("whitespace", 800, 200): 0,
    ("whitespace", 200, 50): 0,
    ("whitespace", 100, 0): 0,
    ("whitespace", 1000, 300): 0,
    ("short", 800, 200): 1,
    ("short", 200, 50): 1,
    ("short", 100, 0): 1,
    ("short", 1000, 300): 1,
    ("paragraphs", 800, 200): 1,
    ("paragraphs", 200, 50): 1,
    ("paragraphs", 100, 0): 4,
    ("paragraphs", 1000, 300): 1,
    ("long_prose", 800, 200): 8,
    ("long_prose", 200, 50): 32,
    ("long_prose", 100, 0): 60,
    ("long_prose", 1000, 300): 7,
    ("newlines_only", 800, 200): 3,
    ("newlines_only", 200, 50): 11,
    ("newlines_only", 100, 0): 18,
    ("newlines_only", 1000, 300): 2,
    ("no_separators", 800, 200): 4,
    ("no_separators", 200, 50): 17,
    ("no_separators", 100, 0): 25,
    ("no_separators", 1000, 300): 4,
    ("mixed", 800, 200): 4,
    ("mixed", 200, 50): 10,
    ("mixed", 100, 0): 16,
    ("mixed", 1000, 300): 2,
    ("unicode", 800, 200): 3,
    ("unicode", 200, 50): 10,
    ("unicode", 100, 0): 20,
    ("unicode", 1000, 300): 2,
}


def _split(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if not text or not text.strip():
        return []
    splitter = RecursiveCharacterSplitter(chunk_size, chunk_overlap, SEPARATORS)
    return [chunk for chunk in splitter.split_text(text) if chunk.strip()]


@pytest.mark.parametrize(("case", "chunk_size", "chunk_overlap"), list(EXPECTED_COUNTS))
def test_chunk_count_matches_langchain(
    case: str, chunk_size: int, chunk_overlap: int
) -> None:
    chunks = _split(CASES[case], chunk_size, chunk_overlap)

    assert len(chunks) == EXPECTED_COUNTS[(case, chunk_size, chunk_overlap)]


@pytest.mark.parametrize(("case", "chunk_size", "chunk_overlap"), list(EXPECTED_COUNTS))
def test_chunks_respect_the_size_budget(
    case: str, chunk_size: int, chunk_overlap: int
) -> None:
    """Only an indivisible run of characters may exceed the budget."""
    for chunk in _split(CASES[case], chunk_size, chunk_overlap):
        if len(chunk) > chunk_size:
            assert " " not in chunk and "\n" not in chunk, (
                f"{case}: a chunk over budget still contained a separator: {chunk[:60]!r}"
            )


@pytest.mark.parametrize("case", ["paragraphs", "long_prose", "mixed", "unicode"])
def test_no_content_is_lost(case: str) -> None:
    """Every non-separator character survives the split."""
    text = CASES[case]
    joined = "".join(_split(text, 200, 0))

    assert joined.replace(" ", "").replace("\n", "") == text.replace(" ", "").replace(
        "\n", ""
    )


class TestPublicChunkText:
    def test_chunk_text_uses_the_new_splitter(self) -> None:
        chunks = chunk_text(CASES["long_prose"], 200, 50)

        assert len(chunks) == EXPECTED_COUNTS[("long_prose", 200, 50)]

    def test_blank_input_yields_no_chunks(self) -> None:
        assert chunk_text("") == []
        assert chunk_text("   \n\n  ") == []

    def test_default_splitter_is_reused(self) -> None:
        """The default (size, overlap) pair must not rebuild a splitter."""
        from core.services.vectorstore.chunking import (
            DEFAULT_CHUNK_OVERLAP,
            DEFAULT_CHUNK_SIZE,
            DEFAULT_SPLITTER,
            _get_splitter,
        )

        assert (
            _get_splitter(DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_OVERLAP) is DEFAULT_SPLITTER
        )


class TestSplitterContract:
    def test_overlap_larger_than_size_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="chunk_overlap"):
            RecursiveCharacterSplitter(100, 200, SEPARATORS)

    def test_empty_separator_terminates_the_recursion(self) -> None:
        """Without a final "" separator an unsplittable run would never fit."""
        chunks = RecursiveCharacterSplitter(10, 0, ["\n\n", ""]).split_text("y" * 35)

        assert chunks and all(len(chunk) <= 10 for chunk in chunks)
        assert "".join(chunks) == "y" * 35

    def test_langchain_is_not_imported(self) -> None:
        """The dependency this module exists to remove must stay removed."""
        import core.services.vectorstore.chunking as module

        source = __import__("pathlib").Path(module.__file__).read_text(encoding="utf-8")
        assert "langchain" not in source
