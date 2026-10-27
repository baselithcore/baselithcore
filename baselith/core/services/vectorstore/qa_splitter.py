"""Question-and-answer chunking.

Prose is chunked by size; an FAQ should not be. Here every question/answer pair
is one chunk, and the *question* is what gets embedded: a user asks in the
shape of a question, so matching it against the stored question retrieves the
right entry far more reliably than matching it against a slice of the answer.
Retrieval still returns the whole pair, answer included.

Three input shapes are recognised, tried in this order:

1. **Tabular**: CSV or TSV with a header naming a ``question`` and an
   ``answer`` column.
2. **Labelled**: lines opening with ``Q:`` / ``Question:`` and ``A:`` /
   ``Answer:`` (an optional list marker or number before the label is fine);
   a value runs until the next label.
3. **Markdown FAQ**: a heading that ends with ``?`` is the question and the
   text under it, up to the next heading, is the answer.

A document with no recognisable pair yields no chunks. The caller decides what
that means; it is not quietly re-chunked as prose, because a mis-formatted FAQ
indexed as prose is the failure this mode exists to avoid.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from typing import Any

from baselith.core.services.vectorstore.splitters import TextChunk

__all__ = [
    "MAX_ANSWER_CHARS",
    "MAX_PAIRS",
    "MAX_QUESTION_CHARS",
    "QAPair",
    "QASplitter",
    "parse_qa_pairs",
    "qa_ingestion_chunks",
]

#: Pairs read from one document; the rest are ignored.
MAX_PAIRS = 5000
#: A longer question is not a question; the pair is dropped.
MAX_QUESTION_CHARS = 1000
#: A longer answer is cut at this length.
MAX_ANSWER_CHARS = 8000

_LABEL_PREFIX = r"^\s*(?:[-*+]\s+|\d+[.)]\s+|#{1,6}\s+)?(?:\*\*)?"
_QUESTION_LABEL = re.compile(
    _LABEL_PREFIX + r"(?:q|question)\s*\d*\s*[:.)\-]\s*(?:\*\*)?\s*(.*)$", re.IGNORECASE
)
_ANSWER_LABEL = re.compile(
    _LABEL_PREFIX + r"(?:a|answer)\s*\d*\s*[:.)\-]\s*(?:\*\*)?\s*(.*)$", re.IGNORECASE
)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class QAPair:
    """One question with its answer."""

    question: str
    answer: str

    def as_text(self) -> str:
        """The pair as stored and shown at retrieval time."""
        return f"Q: {self.question}\nA: {self.answer}"


def _clean(value: str) -> str:
    return "\n".join(line.rstrip() for line in value.strip().splitlines()).strip()


def _keep(question: str, answer: str) -> QAPair | None:
    question, answer = _clean(question), _clean(answer)
    if not question or not answer or len(question) > MAX_QUESTION_CHARS:
        return None
    return QAPair(question, answer[:MAX_ANSWER_CHARS])


def _parse_table(text: str) -> list[QAPair]:
    head = text.lstrip().splitlines()[:1]
    if not head:
        return []
    header = head[0].lower()
    if "question" not in header or "answer" not in header:
        return []
    try:
        dialect = csv.Sniffer().sniff(head[0], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = csv.DictReader(io.StringIO(text.lstrip()), dialect=dialect)
    fields = {(name or "").strip().lower(): name for name in rows.fieldnames or []}
    q_key = next((fields[n] for n in fields if "question" in n), None)
    a_key = next((fields[n] for n in fields if "answer" in n), None)
    if q_key is None or a_key is None:
        return []
    pairs: list[QAPair] = []
    for row in rows:
        pair = _keep(row.get(q_key) or "", row.get(a_key) or "")
        if pair is not None:
            pairs.append(pair)
        if len(pairs) >= MAX_PAIRS:
            break
    return pairs


def _parse_labelled(text: str) -> list[QAPair]:
    pairs: list[QAPair] = []
    question: list[str] = []
    answer: list[str] = []
    state = "idle"  # idle | question | answer
    in_fence = False

    def flush() -> None:
        if question and answer:
            pair = _keep("\n".join(question), "\n".join(answer))
            if pair is not None:
                pairs.append(pair)
        question.clear()
        answer.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        q_match = None if in_fence else _QUESTION_LABEL.match(line)
        a_match = None if in_fence else _ANSWER_LABEL.match(line)
        if q_match and not (state == "question" and a_match):
            flush()
            state = "question"
            question.append(q_match.group(1))
        elif a_match and state == "question":
            state = "answer"
            answer.append(a_match.group(1))
        elif state == "question":
            question.append(line)
        elif state == "answer":
            answer.append(line)
        if len(pairs) >= MAX_PAIRS:
            break
    flush()
    return pairs[:MAX_PAIRS]


def _parse_markdown_faq(text: str) -> list[QAPair]:
    pairs: list[QAPair] = []
    question: str | None = None
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        if question is not None:
            pair = _keep(question, "\n".join(body))
            if pair is not None:
                pairs.append(pair)

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        heading = None if in_fence else _HEADING.match(line)
        if heading:
            flush()
            title = heading.group(2).strip()
            question = title if title.endswith("?") else None
            body = []
        elif question is not None:
            body.append(line)
        if len(pairs) >= MAX_PAIRS:
            break
    flush()
    return pairs[:MAX_PAIRS]


def parse_qa_pairs(text: str) -> list[QAPair]:
    """Extract question/answer pairs from ``text``.

    Args:
        text: A CSV/TSV table, a Q:/A: labelled list, or a markdown FAQ.

    Returns:
        The pairs in document order, at most :data:`MAX_PAIRS`. Empty when no
        shape matches.
    """
    for parser in (_parse_table, _parse_labelled, _parse_markdown_faq):
        pairs = parser(text)
        if pairs:
            return pairs
    return []


class QASplitter:
    """Splitter conforming to the vector store's splitter interface."""

    def split_text(self, text: str) -> list[str]:
        """One ``Q: …\\nA: …`` string per pair."""
        return [pair.as_text() for pair in parse_qa_pairs(text)]

    def split(self, text: str) -> list[TextChunk]:
        """One chunk per pair, with the question in the metadata."""
        return [
            TextChunk(text=pair.as_text(), metadata={"question": pair.question})
            for pair in parse_qa_pairs(text)
        ]


def qa_ingestion_chunks(text: str) -> list[dict[str, Any]]:
    """Pairs in the shape the indexing pipeline takes as precomputed chunks.

    The stored and displayed text is the whole pair; ``embedding_text`` is the
    question alone, so what is searched is the question.
    """
    return [
        {
            "text": pair.as_text(),
            "chunk_body": pair.as_text(),
            "embedding_text": pair.question,
            "original_text": pair.as_text(),
            "question": pair.question,
        }
        for pair in parse_qa_pairs(text)
    ]
