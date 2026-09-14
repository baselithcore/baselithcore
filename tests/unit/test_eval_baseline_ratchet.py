"""Tests for the eval-corpus ratchet (scripts/check_eval_baseline.py)."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_eval_baseline import (
    DATASET_HASH_KEY,
    DATASET_VERSION_KEY,
    check_eval_baseline,
    count_suites,
    dataset_sha256,
    load_baseline,
    load_baseline_document,
    write_baseline,
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


class TestDatasetHash:
    """Counts alone miss *content* drift: a case edited to assert nothing
    keeps the count and silently weakens the gate."""

    def test_hash_is_stable_across_calls(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        assert dataset_sha256(tmp_path) == dataset_sha256(tmp_path)

    def test_hash_changes_when_a_case_body_changes(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        before = dataset_sha256(tmp_path)
        # Same number of cases, different content.
        (tmp_path / "cases" / "s.yaml").write_text("- id: c0\n- id: RENAMED\n")
        assert count_suites(tmp_path)["cases"] == 2
        assert dataset_sha256(tmp_path) != before

    def test_hash_covers_the_file_name_not_just_the_bytes(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        before = dataset_sha256(tmp_path)
        (tmp_path / "cases" / "s.yaml").rename(tmp_path / "cases" / "renamed.yaml")
        assert dataset_sha256(tmp_path) != before


class TestDriftDetection:
    def test_matching_hash_passes(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        baseline = {
            "cases": 2,
            "red_team": 2,
            "runs": 1,
            DATASET_HASH_KEY: dataset_sha256(tmp_path),
        }
        assert check_eval_baseline(tmp_path, baseline) == []

    def test_silent_content_drift_fails(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        baseline = {
            "cases": 2,
            "red_team": 2,
            "runs": 1,
            DATASET_HASH_KEY: dataset_sha256(tmp_path),
        }
        (tmp_path / "cases" / "s.yaml").write_text("- id: c0\n- id: TAMPERED\n")
        violations = check_eval_baseline(tmp_path, baseline)
        assert len(violations) == 1
        assert "dataset" in violations[0].lower()

    def test_baseline_without_a_hash_only_ratchets_counts(self, tmp_path: Path) -> None:
        """A pre-hash baseline must keep working."""
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        assert check_eval_baseline(tmp_path, {"cases": 2}) == []


class TestBaselineDocument:
    def test_write_baseline_stamps_hash_and_version(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        target = tmp_path / "baseline.json"
        document = write_baseline(tmp_path, target)
        assert document["cases"] == 2
        assert document[DATASET_HASH_KEY] == dataset_sha256(tmp_path)
        assert document[DATASET_VERSION_KEY] == 1
        assert check_eval_baseline(tmp_path, document) == []

    def test_version_increments_only_when_the_corpus_changed(
        self, tmp_path: Path
    ) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        target = tmp_path / "baseline.json"
        write_baseline(tmp_path, target)
        unchanged = write_baseline(tmp_path, target)
        assert unchanged[DATASET_VERSION_KEY] == 1
        (tmp_path / "cases" / "s.yaml").write_text("- id: c0\n- id: c1\n- id: c2\n")
        bumped = write_baseline(tmp_path, target)
        assert bumped[DATASET_VERSION_KEY] == 2

    def test_load_baseline_returns_counts_only(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        target = tmp_path / "baseline.json"
        write_baseline(tmp_path, target)
        counts = load_baseline(target)
        assert set(counts) == {"cases", "red_team", "runs"}
        assert all(isinstance(v, int) for v in counts.values())

    def test_load_baseline_document_keeps_metadata(self, tmp_path: Path) -> None:
        _seed(tmp_path, cases=2, red_team=2, runs=1)
        target = tmp_path / "baseline.json"
        write_baseline(tmp_path, target)
        document = load_baseline_document(target)
        assert DATASET_HASH_KEY in document
        assert DATASET_VERSION_KEY in document


class TestRepoBaselineMetadata:
    def test_committed_baseline_carries_hash_and_version(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        evals_dir = repo_root / "evals"
        document = load_baseline_document(evals_dir / "baseline.json")
        assert document[DATASET_HASH_KEY] == dataset_sha256(evals_dir)
        assert isinstance(document[DATASET_VERSION_KEY], int)
        assert check_eval_baseline(evals_dir, document) == []
