"""Judge sampling, median gating and bounded judge fan-out.

A single judge sample is a coin flip: LLM scoring is nondeterministic, so one
low draw failed a good case and one high draw passed a bad one. The scheduled
run therefore scores each case ``k`` times (``EVAL_JUDGE_SAMPLES``, default 3)
and gates on the **median**. Judge calls are bounded by
``EVAL_JUDGE_MAX_PARALLEL`` (default 4) rather than gathered all at once.
"""

from __future__ import annotations

import asyncio

import pytest

from core.evaluation.regression_runner import (
    DEFAULT_JUDGE_MAX_PARALLEL,
    DEFAULT_JUDGE_SAMPLES,
    RecordedRun,
    run_regression_async,
)


def _case(case_id="c1", **extra):
    return {"case_id": case_id, "input": "question?", **extra}


def _run(case_id="c1", output="a fine answer"):
    return RecordedRun(
        case_id=case_id, output_text=output, trajectory=[], latency_ms=10
    )


class ScriptedJudge:
    """Returns the next score from a script; raises for ``None`` entries."""

    def __init__(self, scores):
        self._scores = list(scores)
        self.calls = 0

    async def evaluate(self, response, query, context=None):
        from types import SimpleNamespace

        index = min(self.calls, len(self._scores) - 1)
        self.calls += 1
        value = self._scores[index]
        if value is None:
            raise RuntimeError("provider down")
        return SimpleNamespace(score=value)


class TestDefaults:
    def test_default_constants(self) -> None:
        assert DEFAULT_JUDGE_SAMPLES == 3
        assert DEFAULT_JUDGE_MAX_PARALLEL == 4

    def test_settings_expose_the_knobs(self) -> None:
        from core.config.evaluation import EvaluationConfig

        config = EvaluationConfig()
        assert config.judge_samples == 3
        assert config.judge_max_parallel == 4


class TestMedianGating:
    async def test_single_sample_is_still_supported(self) -> None:
        judge = ScriptedJudge([0.9])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=1
        )
        assert judge.calls == 1
        assert report.judge_scores == {"c1": 0.9}

    async def test_k_samples_are_taken(self) -> None:
        judge = ScriptedJudge([0.9, 0.8, 0.7])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=3
        )
        assert judge.calls == 3
        assert report.judge_scores == {"c1": 0.8}  # median, not mean/first

    async def test_median_ignores_one_outlier_low_draw(self) -> None:
        """One unlucky low sample must not fail an otherwise good case."""
        judge = ScriptedJudge([0.1, 0.9, 0.95])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=3, judge_min_score=0.6
        )
        assert report.passed == 1
        assert report.judge_scores["c1"] == pytest.approx(0.9)

    async def test_median_below_threshold_fails_the_case(self) -> None:
        judge = ScriptedJudge([0.2, 0.3, 0.9])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=3, judge_min_score=0.6
        )
        assert report.passed == 0
        assert report.judge_scores["c1"] == pytest.approx(0.3)

    async def test_even_sample_count_averages_the_middle_pair(self) -> None:
        judge = ScriptedJudge([0.4, 0.6, 0.8, 0.9])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=4, judge_min_score=0.0
        )
        assert report.judge_scores["c1"] == pytest.approx(0.7)

    async def test_partial_errors_use_the_surviving_samples(self) -> None:
        judge = ScriptedJudge([0.9, None, 0.7])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=3, judge_min_score=0.6
        )
        assert report.passed == 1
        assert report.judge_scores["c1"] == pytest.approx(0.8)
        assert report.judge_errors == []  # partial flake is not an error

    async def test_all_samples_error_keeps_deterministic_verdict(self) -> None:
        judge = ScriptedJudge([None, None, None])
        report = await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=3
        )
        assert report.passed == 1
        assert report.judge_errors == ["c1"]
        assert report.judge_scores == {}

    async def test_zero_samples_degrades_to_one(self) -> None:
        judge = ScriptedJudge([0.9])
        await run_regression_async(
            [_case()], {"c1": _run()}, judge=judge, judge_samples=0
        )
        assert judge.calls == 1

    async def test_default_sample_count_comes_from_settings(self, monkeypatch) -> None:
        import core.evaluation.regression_runner as runner

        monkeypatch.setattr(runner, "_judge_defaults", lambda: (2, 4))
        judge = ScriptedJudge([0.9, 0.9])
        await run_regression_async([_case()], {"c1": _run()}, judge=judge)
        assert judge.calls == 2


class TestBoundedFanOut:
    async def test_judge_calls_are_concurrency_bounded(self) -> None:
        live = 0
        peak = 0

        class SlowJudge:
            async def evaluate(self, response, query, context=None):
                nonlocal live, peak
                from types import SimpleNamespace

                live += 1
                peak = max(peak, live)
                try:
                    await asyncio.sleep(0.02)
                finally:
                    live -= 1
                return SimpleNamespace(score=0.9)

        cases = [_case(f"c{i}") for i in range(8)]
        recorded = {f"c{i}": _run(f"c{i}") for i in range(8)}
        report = await run_regression_async(
            cases,
            recorded,
            judge=SlowJudge(),
            judge_samples=2,
            judge_concurrency=3,
        )
        assert report.passed == 8
        assert peak <= 3  # 16 judge calls, never more than 3 at once


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


class TestDuplicateCaseIds:
    """Two cases sharing a ``case_id`` must be judged independently.

    The sample results were grouped by ``case_id``, so a corpus with a
    duplicated id merged both cases' draws into one median and scored both
    from it — a silent cross-contamination between distinct cases.
    """

    async def test_duplicate_ids_do_not_share_a_median(self) -> None:
        # Two cases with the SAME id: the first draws high, the second low.
        judge = ScriptedJudge([0.9, 0.9, 0.9, 0.1, 0.1, 0.1])
        cases = [_case("dup"), _case("dup")]
        report = await run_regression_async(
            cases,
            {"dup": _run("dup")},
            judge=judge,
            judge_samples=3,
            judge_min_score=0.5,
        )
        assert judge.calls == 6  # 2 cases x 3 samples, not 3 shared draws
        # Verdicts differ: the high-scoring case passes, the low one fails.
        assert [r.passed for r in report.results] == [True, False]

    async def test_report_is_still_aligned_with_the_deterministic_pass(
        self,
    ) -> None:
        judge = ScriptedJudge([0.9])
        cases = [_case("a"), _case("dup"), _case("dup"), _case("b")]
        recorded = {
            "a": _run("a"),
            "dup": _run("dup"),
            "b": _run("b"),
        }
        report = await run_regression_async(
            cases, recorded, judge=judge, judge_samples=1
        )
        assert [r.case_id for r in report.results] == ["a", "dup", "dup", "b"]
        assert report.total == 4
