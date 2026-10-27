"""Question/answer chunking."""

import pytest

from baselith.core.services.vectorstore import qa_splitter
from baselith.core.services.vectorstore.qa_splitter import (
    MAX_ANSWER_CHARS,
    MAX_QUESTION_CHARS,
    QAPair,
    QASplitter,
    parse_qa_pairs,
    qa_ingestion_chunks,
)

pytestmark = [pytest.mark.unit]


class TestLabelled:
    def test_q_and_a_lines_with_multiline_answers(self):
        text = "Q: How do I reset?\nA: Open Settings.\nThen Security.\n\nQ: Price?\nA: 10 EUR\n"
        assert parse_qa_pairs(text) == [
            QAPair("How do I reset?", "Open Settings.\nThen Security."),
            QAPair("Price?", "10 EUR"),
        ]

    @pytest.mark.parametrize(
        "text",
        [
            "Question: Why?\nAnswer: Because.",
            "1. Q: Why?\n   A: Because.",
            "- Q1. Why?\n- A1. Because.",
            "**Q:** Why?\n**A:** Because.",
            "q: Why?\na: Because.",
        ],
    )
    def test_label_variants(self, text):
        assert parse_qa_pairs(text) == [QAPair("Why?", "Because.")]

    def test_multiline_question(self):
        text = "Q: First line\nsecond line?\nA: yes"
        assert parse_qa_pairs(text) == [QAPair("First line\nsecond line?", "yes")]

    def test_question_without_answer_is_dropped(self):
        assert parse_qa_pairs("Q: lonely?\n\nQ: Why?\nA: Because.") == [
            QAPair("Why?", "Because.")
        ]

    def test_labels_inside_a_code_fence_are_content(self):
        text = "Q: Show a sample?\nA: Here:\n```\nQ: not a question\nA: not an answer\n```\nDone."
        (pair,) = parse_qa_pairs(text)
        assert pair.question == "Show a sample?"
        assert "Q: not a question" in pair.answer and pair.answer.endswith("Done.")


class TestTable:
    def test_csv_with_quoted_commas(self):
        text = 'question,answer\nWhat is X?,"A thing, really"\nWho?,Me\n'
        assert parse_qa_pairs(text) == [
            QAPair("What is X?", "A thing, really"),
            QAPair("Who?", "Me"),
        ]

    def test_tsv_and_semicolons_and_extra_columns(self):
        tsv = "id\tQuestion\tAnswer\n1\tWhy?\tBecause\n"
        assert parse_qa_pairs(tsv) == [QAPair("Why?", "Because")]
        semi = "Question;Answer\nWhy?;Because\n"
        assert parse_qa_pairs(semi) == [QAPair("Why?", "Because")]

    def test_rows_with_a_blank_side_are_skipped(self):
        assert parse_qa_pairs("question,answer\nWhy?,\n,Because\nOk?,Yes\n") == [
            QAPair("Ok?", "Yes")
        ]

    def test_a_header_without_both_columns_is_not_a_table(self):
        assert parse_qa_pairs("question,reply\nWhy?,Because\n") == []


class TestMarkdownFaq:
    def test_headings_ending_in_a_question_mark(self):
        text = "# FAQ\n\n## How to pay?\nUse a card.\n\n## Not a question\nignored\n\n### Refunds?\nWithin 14 days.\n"
        assert parse_qa_pairs(text) == [
            QAPair("How to pay?", "Use a card."),
            QAPair("Refunds?", "Within 14 days."),
        ]

    def test_hash_lines_inside_a_fence_are_not_headings(self):
        text = "## Install?\n```\n# comment?\nrun it\n```\nthen go\n"
        (pair,) = parse_qa_pairs(text)
        assert "# comment?" in pair.answer and pair.answer.endswith("then go")


class TestBounds:
    def test_prose_yields_nothing(self):
        assert parse_qa_pairs("Just some prose. Nothing to see?") == []
        assert parse_qa_pairs("") == []

    def test_oversized_question_is_dropped_and_answer_is_cut(self):
        long_q = (
            "Q: "
            + "x" * (MAX_QUESTION_CHARS + 1)
            + "\nA: a\nQ: ok?\nA: "
            + "y" * (MAX_ANSWER_CHARS + 50)
        )
        (pair,) = parse_qa_pairs(long_q)
        assert pair.question == "ok?" and len(pair.answer) == MAX_ANSWER_CHARS

    def test_pair_count_is_capped(self, monkeypatch):
        monkeypatch.setattr(qa_splitter, "MAX_PAIRS", 3)
        text = "".join(f"Q: q{i}?\nA: a{i}\n" for i in range(10))
        assert len(parse_qa_pairs(text)) == 3
        csv_text = "question,answer\n" + "".join(f"q{i}?,a{i}\n" for i in range(10))
        assert len(parse_qa_pairs(csv_text)) == 3
        faq = "".join(f"## q{i}?\na{i}\n" for i in range(10))
        assert len(parse_qa_pairs(faq)) == 3


def test_splitter_interface():
    text = "Q: Why?\nA: Because."
    splitter = QASplitter()
    assert splitter.split_text(text) == ["Q: Why?\nA: Because."]
    (chunk,) = splitter.split(text)
    assert chunk.text == "Q: Why?\nA: Because." and chunk.metadata == {
        "question": "Why?"
    }


def test_ingestion_chunks_embed_the_question_and_store_the_pair():
    (chunk,) = qa_ingestion_chunks("Q: Why?\nA: Because.")
    assert chunk["embedding_text"] == "Why?"
    assert chunk["text"] == chunk["chunk_body"] == "Q: Why?\nA: Because."
    assert chunk["question"] == "Why?"
