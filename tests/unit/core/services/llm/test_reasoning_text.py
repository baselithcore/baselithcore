"""Stripping a thinking model's reasoning out of the answer text.

A vLLM server started without ``--reasoning-parser`` returns a thinking
model's (Qwen3, DeepSeek-R1) reasoning in the answer itself: the chat template
opens the ``<think>`` block in the prompt, so the completion is
``reasoning…</think>\\n\\nanswer`` — no opening tag, one closing tag. Shapes
below are the ones the real server produced.
"""

from __future__ import annotations

import pytest

from core.services.llm.reasoning_text import ReasoningStreamFilter, strip_reasoning

_REAL = "Here's a thinking process:\n\n1. **Analyze**\n   - 4\n✅\n</think>\n\n4"


class TestStrip:
    def test_keeps_only_what_follows_the_closing_tag(self):
        assert strip_reasoning(_REAL) == "4"

    def test_an_explicit_opening_tag_is_handled_too(self):
        assert strip_reasoning("<think>\nhmm\n</think>\n\nanswer") == "answer"

    def test_text_without_reasoning_is_untouched(self):
        assert strip_reasoning("  plain answer \n") == "  plain answer \n"

    def test_a_truncated_reasoning_block_yields_no_answer(self):
        """Cut off by the token cap mid-thought: there is no answer to show."""
        assert strip_reasoning("<think>\nstill thinking about") == ""

    @pytest.mark.parametrize("value", ["", None])
    def test_empty_values_pass_through(self, value):
        assert strip_reasoning(value) == (value or "")


def _run(chunks: list[str], **kw) -> str:
    f = ReasoningStreamFilter(**kw)
    out = "".join(f.feed(c) for c in chunks)
    return out + f.finish()


class TestStream:
    def test_reasoning_is_held_back_and_dropped(self):
        chunks = [
            "Here's a thinking",
            " process:\n1. x\n</thi",
            "nk>\n\n",
            "4",
            " done",
        ]
        assert _run(chunks) == "4 done"

    def test_the_answer_streams_once_reasoning_ends(self):
        f = ReasoningStreamFilter()
        assert f.feed("thinking…</think>\n\nA") == "A"
        assert f.feed("B") == "B"
        assert f.finish() == ""

    def test_a_stream_without_reasoning_is_released_at_the_end(self):
        assert _run(["plain ", "answer"]) == "plain answer"

    def test_a_long_reasoning_free_stream_is_released_past_the_cap(self):
        f = ReasoningStreamFilter(max_buffer=10)
        assert f.feed("12345") == ""
        assert f.feed("678901") == "12345678901"
        assert f.feed("x") == "x"

    def test_a_server_that_separates_reasoning_turns_the_filter_off(self):
        f = ReasoningStreamFilter()
        f.passthrough()
        assert f.feed("an answer mentioning </think> literally") == (
            "an answer mentioning </think> literally"
        )

    def test_a_truncated_reasoning_stream_yields_nothing(self):
        assert _run(["<think>\n", "still thinking"]) == ""
