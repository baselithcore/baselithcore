"""Lesson compaction: failures survive as one line, not as transcripts."""

from core.loops.lessons import LessonLog, compact_evidence


class TestCompactEvidence:
    def test_keeps_the_failure_line(self):
        summary = compact_evidence("noise\nFAILED test_a.py::x - boom\nmore noise")
        assert "test_a.py" in summary
        assert "more noise" not in summary

    def test_truncates_long_output(self):
        summary = compact_evidence("FAILED " + "x" * 5000, max_chars=100)
        assert len(summary) <= 100

    def test_empty_evidence_is_reported_not_faked(self):
        assert compact_evidence("") == "(no failure detail captured)"


class TestLessonLog:
    def test_records_one_lesson_per_attempt(self):
        log = LessonLog()
        log.record(1, "FAILED a - boom", "aaa111")
        log.record(2, "FAILED b - bang", "bbb222")
        assert len(log) == 2
        rendered = log.render()
        assert "Attempt 1 failed [aaa111]" in rendered
        assert "Attempt 2 failed [bbb222]" in rendered

    def test_render_is_empty_before_any_failure(self):
        assert LessonLog().render() == ""

    def test_only_the_most_recent_lessons_are_fed_forward(self):
        log = LessonLog(max_lessons=2)
        for i in range(1, 6):
            log.record(i, f"FAILED case{i}", f"fp{i}")
        rendered = log.render()
        assert "Attempt 4" in rendered and "Attempt 5" in rendered
        assert "Attempt 1" not in rendered
        # Nothing is lost from the record itself — only from the fed context.
        assert len(log) == 5

    def test_clear_drops_everything(self):
        log = LessonLog()
        log.record(1, "FAILED a", "fp")
        log.clear()
        assert len(log) == 0 and log.render() == ""
