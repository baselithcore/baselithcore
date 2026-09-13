"""Unit tests for the neutral :class:`Usage` token-accounting record."""

from types import SimpleNamespace

import pytest

from core.services.llm.usage import Usage


class TestUsageBasics:
    def test_defaults_are_zero_and_not_estimated(self):
        usage = Usage()
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0
        assert usage.estimated is False
        assert usage.total == 0

    def test_total_sums_every_billed_bucket(self):
        usage = Usage(
            input_tokens=10,
            output_tokens=5,
            cache_read_tokens=100,
            cache_write_tokens=7,
        )
        assert usage.total == 122

    def test_prompt_tokens_excludes_output(self):
        usage = Usage(input_tokens=10, output_tokens=5, cache_read_tokens=100)
        assert usage.prompt_tokens == 110

    def test_is_frozen(self):
        usage = Usage(input_tokens=1)
        with pytest.raises(Exception):
            usage.input_tokens = 2  # type: ignore[misc]

    def test_empty_reports_nothing_recorded(self):
        assert Usage().is_empty is True
        assert Usage(output_tokens=1).is_empty is False


class TestUsageFromAnthropic:
    def test_reads_all_four_counters(self):
        raw = SimpleNamespace(
            input_tokens=10,
            output_tokens=20,
            cache_creation_input_tokens=30,
            cache_read_input_tokens=40,
        )
        usage = Usage.from_anthropic(raw)
        assert usage == Usage(
            input_tokens=10,
            output_tokens=20,
            cache_write_tokens=30,
            cache_read_tokens=40,
        )
        # Cache tokens stay separate from input: they are billed at a
        # different rate and summing them destroys that.
        assert usage.input_tokens == 10
        assert usage.total == 100

    def test_missing_cache_counters_default_to_zero(self):
        raw = SimpleNamespace(input_tokens=3, output_tokens=4)
        assert Usage.from_anthropic(raw) == Usage(input_tokens=3, output_tokens=4)

    def test_none_usage_is_empty(self):
        assert Usage.from_anthropic(None) == Usage()

    def test_none_valued_counters_are_zero(self):
        raw = SimpleNamespace(
            input_tokens=1,
            output_tokens=2,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        )
        assert Usage.from_anthropic(raw) == Usage(input_tokens=1, output_tokens=2)


class TestUsageFromOpenAI:
    def test_reads_cached_prompt_tokens(self):
        raw = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        )
        usage = Usage.from_openai(raw)
        # OpenAI counts cached tokens INSIDE prompt_tokens, so the neutral
        # record splits them out instead of double counting.
        assert usage.input_tokens == 20
        assert usage.cache_read_tokens == 80
        assert usage.output_tokens == 20
        assert usage.total == 120

    def test_without_details_all_prompt_tokens_are_input(self):
        raw = SimpleNamespace(prompt_tokens=50, completion_tokens=5)
        usage = Usage.from_openai(raw)
        assert usage == Usage(input_tokens=50, output_tokens=5)

    def test_none_usage_is_empty(self):
        assert Usage.from_openai(None) == Usage()


class TestUsageEstimate:
    def test_estimate_marks_the_record(self):
        usage = Usage.estimate(input_tokens=7, output_tokens=3)
        assert usage.estimated is True
        assert usage.total == 10

    def test_merge_adds_counters_and_keeps_estimated_flag(self):
        first = Usage(input_tokens=10, output_tokens=5)
        second = Usage(input_tokens=1, output_tokens=2, cache_read_tokens=3)
        merged = first.merge(second)
        assert merged == Usage(input_tokens=11, output_tokens=7, cache_read_tokens=3)
        assert merged.estimated is False
        assert first.merge(Usage.estimate(1, 1)).estimated is True
