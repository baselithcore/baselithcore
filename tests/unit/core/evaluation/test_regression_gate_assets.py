"""Guard for the checked-in eval regression corpus (evals/).

Keeps ``evals/cases/`` and ``evals/runs/recorded_runs.json`` honest before CI:
every case must have a recording, every recording must pass its case, and the
CLI gate must exit 0 on the shipped corpus. If a flow legitimately changes,
update the recording in the same change as the case.
"""

from pathlib import Path

from core.evaluation.regression_runner import (
    load_cases,
    load_recorded_runs,
    run_regression,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CASES_DIR = REPO_ROOT / "evals" / "cases"
RUNS_FILE = REPO_ROOT / "evals" / "runs" / "recorded_runs.json"


class TestRegressionGateAssets:
    def test_corpus_loads(self):
        cases = load_cases(CASES_DIR)
        assert len(cases) >= 10
        recorded = load_recorded_runs(RUNS_FILE)
        assert set(recorded) == {c["case_id"] for c in cases}

    def test_every_recording_passes_its_case(self):
        report = run_regression(
            load_cases(CASES_DIR), load_recorded_runs(RUNS_FILE), threshold=1.0
        )
        failing = [r.case_id for r in report.results if not r.passed]
        assert report.meets_threshold, (
            f"checked-in recordings must pass their cases; failing: {failing} "
            f"violations: {[(r.case_id, r.violations) for r in report.results if not r.passed]}"
        )

    def test_cli_gate_exits_zero(self):
        import scripts.run_regression_evals as gate

        assert gate.main([]) == 0

    def test_cli_gate_fails_below_threshold(self, tmp_path):
        import json

        import scripts.run_regression_evals as gate

        # A run that violates its case (forbidden tool called).
        (tmp_path / "cases").mkdir()
        (tmp_path / "cases" / "c.yaml").write_text(
            "- case_id: x\n  input: q\n  forbidden_tools: [execute_code]\n",
            encoding="utf-8",
        )
        runs = [
            {
                "case_id": "x",
                "output_text": "done",
                "trajectory": [{"name": "execute_code", "args": {}}],
                "latency_ms": 10,
            }
        ]
        runs_file = tmp_path / "runs.json"
        runs_file.write_text(json.dumps(runs), encoding="utf-8")
        assert (
            gate.main(["--cases", str(tmp_path / "cases"), "--runs", str(runs_file)])
            == 1
        )
