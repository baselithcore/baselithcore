"""Unit tests for ``core.evaluation.regression_runner``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.evaluation.regression_runner import (
    DEFAULT_PASS_THRESHOLD,
    RecordedRun,
    RegressionLoadError,
    RegressionReport,
    load_cases,
    load_recorded_runs,
    run_regression,
)


def _write_case_file(
    dirpath: Path,
    name: str,
    payload: str,
) -> Path:
    p = dirpath / name
    p.write_text(payload, encoding="utf-8")
    return p


class TestLoadCases:
    def test_load_single_case_mapping(self, tmp_path: Path) -> None:
        _write_case_file(
            tmp_path,
            "c1.yaml",
            """
case_id: hello
input: say hi
expected_keywords: [hi]
""".strip(),
        )
        cases = load_cases(tmp_path)
        assert len(cases) == 1
        assert cases[0]["case_id"] == "hello"

    def test_load_list_of_cases(self, tmp_path: Path) -> None:
        _write_case_file(
            tmp_path,
            "many.yaml",
            """
- case_id: a
  input: one
- case_id: b
  input: two
""".strip(),
        )
        cases = load_cases(tmp_path)
        assert {c["case_id"] for c in cases} == {"a", "b"}

    def test_unknown_field_rejected(self, tmp_path: Path) -> None:
        _write_case_file(
            tmp_path,
            "bad.yaml",
            """
case_id: x
input: y
malicious: payload
""".strip(),
        )
        with pytest.raises(RegressionLoadError):
            load_cases(tmp_path)

    def test_missing_case_id_rejected(self, tmp_path: Path) -> None:
        _write_case_file(tmp_path, "bad.yaml", "input: only\n")
        with pytest.raises(RegressionLoadError):
            load_cases(tmp_path)

    def test_empty_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(RegressionLoadError):
            load_cases(tmp_path)

    def test_missing_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(RegressionLoadError):
            load_cases(tmp_path / "nope")


class TestLoadRecordedRuns:
    def test_load_minimal_runs(self, tmp_path: Path) -> None:
        p = tmp_path / "runs.json"
        p.write_text(
            json.dumps(
                [
                    {
                        "case_id": "a",
                        "output_text": "result",
                        "trajectory": [{"name": "search"}],
                        "latency_ms": 100,
                    }
                ]
            ),
            encoding="utf-8",
        )
        runs = load_recorded_runs(p)
        assert "a" in runs
        assert isinstance(runs["a"], RecordedRun)
        assert runs["a"].output_text == "result"

    def test_duplicate_case_id_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "runs.json"
        p.write_text(
            json.dumps(
                [
                    {"case_id": "a", "output_text": "x"},
                    {"case_id": "a", "output_text": "y"},
                ]
            ),
            encoding="utf-8",
        )
        with pytest.raises(RegressionLoadError):
            load_recorded_runs(p)

    def test_top_level_must_be_list(self, tmp_path: Path) -> None:
        p = tmp_path / "runs.json"
        p.write_text(json.dumps({"x": 1}), encoding="utf-8")
        with pytest.raises(RegressionLoadError):
            load_recorded_runs(p)

    def test_missing_case_id_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "runs.json"
        p.write_text(json.dumps([{"output_text": "x"}]), encoding="utf-8")
        with pytest.raises(RegressionLoadError):
            load_recorded_runs(p)


class TestRunRegression:
    def test_all_passing_meets_threshold(self) -> None:
        cases = [
            {"case_id": "a", "expected_keywords": ["ok"]},
            {"case_id": "b", "expected_keywords": ["ok"]},
        ]
        runs = {
            "a": RecordedRun(
                case_id="a", output_text="ok", trajectory=[], latency_ms=0
            ),
            "b": RecordedRun(
                case_id="b", output_text="ok", trajectory=[], latency_ms=0
            ),
        }
        report = run_regression(cases, runs)
        assert isinstance(report, RegressionReport)
        assert report.pass_rate == 1.0
        assert report.meets_threshold
        assert report.failed == 0

    def test_failing_case_lowers_rate(self) -> None:
        cases = [
            {"case_id": "a", "expected_keywords": ["mongodb"]},
            {"case_id": "b", "expected_keywords": ["ok"]},
        ]
        runs = {
            "a": RecordedRun(
                case_id="a",
                output_text="postgres only",
                trajectory=[],
                latency_ms=0,
            ),
            "b": RecordedRun(
                case_id="b", output_text="ok", trajectory=[], latency_ms=0
            ),
        }
        report = run_regression(cases, runs, threshold=0.99)
        assert report.failed == 1
        assert report.pass_rate == 0.5
        assert not report.meets_threshold

    def test_missing_recorded_run_counts_as_failure(self) -> None:
        cases = [{"case_id": "a", "expected_keywords": ["ok"]}]
        report = run_regression(cases, {})
        assert report.failed == 1
        assert report.passed == 0

    def test_to_json_contains_summary(self) -> None:
        cases = [{"case_id": "a"}]
        runs = {
            "a": RecordedRun(case_id="a", output_text="", trajectory=[], latency_ms=0)
        }
        report = run_regression(cases, runs)
        text = report.to_json()
        payload = json.loads(text)
        assert payload["total"] == 1
        assert payload["pass_rate"] == 1.0
        assert payload["meets_threshold"] is True

    def test_default_threshold_constant(self) -> None:
        assert 0.0 <= DEFAULT_PASS_THRESHOLD <= 1.0
        assert DEFAULT_PASS_THRESHOLD >= 0.8
