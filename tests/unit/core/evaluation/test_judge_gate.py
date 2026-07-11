"""LLM-as-judge gate in the regression runner (opt-in, flake-safe)."""

import pytest

from core.evaluation.regression_runner import (
    RecordedRun,
    run_regression_async,
)


def _case(case_id="c1", **extra):
    return {"case_id": case_id, "input": "question?", **extra}


def _run(case_id="c1", output="a fine answer"):
    return RecordedRun(
        case_id=case_id, output_text=output, trajectory=[], latency_ms=10
    )


class StubJudge:
    def __init__(self, score=0.9, error=None):
        self.score = score
        self.error = error
        self.calls = []

    async def evaluate(self, response, query, context=None):
        self.calls.append((response, query))
        if self.error:
            raise self.error
        from types import SimpleNamespace

        return SimpleNamespace(score=self.score)


async def test_without_judge_identical_to_sync_runner():
    report = await run_regression_async([_case()], {"c1": _run()})
    assert report.passed == 1
    assert report.judge_scores == {}


async def test_judge_high_score_keeps_pass():
    judge = StubJudge(score=0.95)
    report = await run_regression_async([_case()], {"c1": _run()}, judge=judge)
    assert report.passed == 1
    assert report.judge_scores == {"c1": 0.95}
    assert judge.calls == [("a fine answer", "question?")]


async def test_judge_low_score_fails_case():
    report = await run_regression_async(
        [_case()], {"c1": _run()}, judge=StubJudge(score=0.2)
    )
    assert report.passed == 0
    assert report.failed == 1
    assert report.judge_scores == {"c1": 0.2}
    assert report.meets_threshold is False


async def test_judge_error_keeps_deterministic_verdict():
    judge = StubJudge(error=RuntimeError("provider down"))
    report = await run_regression_async([_case()], {"c1": _run()}, judge=judge)
    # Flaky judge must never turn CI red on its own.
    assert report.passed == 1
    assert report.judge_errors == ["c1"]
    assert report.judge_scores == {}


async def test_judge_skips_deterministically_failed_cases():
    judge = StubJudge(score=0.99)
    case = _case(expected_keywords=["absent-keyword"])
    report = await run_regression_async([case], {"c1": _run()}, judge=judge)
    assert report.passed == 0
    assert judge.calls == []  # no wasted LLM call on an already-failed case


async def test_custom_min_score_threshold():
    report = await run_regression_async(
        [_case()], {"c1": _run()}, judge=StubJudge(score=0.75), judge_min_score=0.8
    )
    assert report.passed == 0
    report2 = await run_regression_async(
        [_case()], {"c1": _run()}, judge=StubJudge(score=0.75), judge_min_score=0.7
    )
    assert report2.passed == 1


async def test_judge_fields_serialized_in_report_json():
    report = await run_regression_async(
        [_case()], {"c1": _run()}, judge=StubJudge(score=0.4)
    )
    assert '"judge_scores"' in report.to_json()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
