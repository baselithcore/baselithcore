"""Tests for core/orchestration/tool_output.py"""

from core.orchestration.tool_output import (
    DEFAULT_TOOL_OUTPUT_MAX_CHARS,
    truncate_tool_output,
)


class TestTruncateToolOutput:
    def test_short_output_passthrough(self):
        assert truncate_tool_output("hello") == "hello"

    def test_at_limit_passthrough(self):
        text = "x" * 100
        assert truncate_tool_output(text, max_chars=100) == text

    def test_long_output_truncated_head_and_tail(self):
        text = "A" * 500 + "B" * 500  # 1000 chars
        out = truncate_tool_output(text, max_chars=300)
        # Head and tail are preserved, middle replaced with a marker.
        assert out.startswith("A")
        assert out.endswith("B")
        assert "[truncated" in out
        # Result stays close to budget (+ marker), never the full 1000 chars.
        assert len(out) < len(text)

    def test_marker_reports_dropped_count(self):
        text = "z" * 1000
        out = truncate_tool_output(text, max_chars=300)
        # 1000 - head(200) - tail(100) = 700 dropped.
        assert "truncated 700 chars" in out

    def test_disabled_with_zero(self):
        text = "q" * 5000
        assert truncate_tool_output(text, max_chars=0) == text

    def test_disabled_with_negative(self):
        text = "q" * 5000
        assert truncate_tool_output(text, max_chars=-1) == text

    def test_default_max_chars_is_positive(self):
        assert DEFAULT_TOOL_OUTPUT_MAX_CHARS > 0

    def test_uses_default_when_none(self):
        text = "m" * (DEFAULT_TOOL_OUTPUT_MAX_CHARS + 5000)
        out = truncate_tool_output(text)
        assert len(out) < len(text)
        assert "[truncated" in out
