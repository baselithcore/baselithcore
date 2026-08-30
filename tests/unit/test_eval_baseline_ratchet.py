"""Tests for the eval-corpus ratchet (scripts/check_eval_baseline.py)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_eval_baseline import (
    check_eval_baseline,
    count_suites,
    load_baseline,
)


def _seed(root: Path, cases: int, red_team: int, runs: int) -> None:
    (root / "cases").mkdir(parents=True)
    (root / "red_team").mkdir()
    (root / "runs").mkdir()
    (root / "cases" / "s.yaml").write_text(
        "".join(f"- id: c{i}\n" for i in range(cases))
    )
    (root / "red_team" / "s.yaml").write_text(
        "".join(f"- id: r{i}\n" for i in range(red_team))
    )
    (root / "runs" / "r.json").write_text(
        json.dumps([{"id": f"run{i}"} for i in range(runs)])
    )


class TestCounting:
    def test_counts_all_suites(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=3, red_team=5, runs=2)
        counts = count_suites(tmp_path)
        assert counts["cases"] == 3
        assert counts["red_team"] == 5
        assert counts["runs"] == 2


class TestRatchet:
    def test_equal_or_growing_corpus_passes(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=3, red_team=5, runs=2)
        baseline = {"cases": 3, "red_team": 4, "runs": 2}
        assert check_eval_baseline(tmp_path, baseline) == []

    def test_shrunk_suite_fails(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=5, runs=2)
        baseline = {"cases": 3, "red_team": 5, "runs": 2}
        violations = check_eval_baseline(tmp_path, baseline)
        assert len(violations) == 1
        assert "cases" in violations[0]

    def test_repo_baseline_matches_reality(self) -> None:
        """The committed baseline must never exceed the actual corpus."""
        repo_root = Path(__file__).resolve().parents[2]
        evals_dir = repo_root / "evals"
        baseline = load_baseline(evals_dir / "baseline.json")
        assert baseline, "evals/baseline.json missing or empty"
        assert check_eval_baseline(evals_dir, baseline) == []
