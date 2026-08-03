"""Tests for the bias-examination CI gate (scripts/run_fairness_evals.py)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "run_fairness_evals", REPO_ROOT / "scripts" / "run_fairness_evals.py"
)
assert _SPEC and _SPEC.loader
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def _write(directory: Path, name: str, payload: dict) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _balanced(n: int = 10) -> list[dict]:
    samples = []
    for group in ("a", "b"):
        for i in range(n):
            samples.append(
                {"group": group, "prediction": i % 2 == 0, "label": i % 2 == 0}
            )
    return samples


class TestShippedFixture:
    def test_the_repo_fixture_passes(self):
        assert gate.main([]) == 0

    def test_the_fixture_directory_is_not_empty(self):
        files = list((REPO_ROOT / "evals" / "fairness").glob("*.json"))
        assert files, "the gate is meaningless without at least one dataset"


class TestGateOutcome:
    def test_balanced_dataset_passes(self, tmp_path):
        _write(tmp_path, "ok", {"name": "ok", "samples": _balanced()})
        assert gate.main(["--datasets", str(tmp_path)]) == 0

    def test_skewed_dataset_fails(self, tmp_path):
        samples = [{"group": "a", "prediction": True} for _ in range(10)]
        samples += [{"group": "b", "prediction": i == 0} for i in range(10)]
        _write(tmp_path, "skewed", {"name": "skewed", "samples": samples})
        assert gate.main(["--datasets", str(tmp_path)]) == 1

    def test_thresholds_are_read_from_the_dataset(self, tmp_path):
        samples = [{"group": "a", "prediction": True} for _ in range(10)]
        samples += [{"group": "b", "prediction": i < 5} for i in range(10)]
        _write(
            tmp_path,
            "loose",
            {
                "name": "loose",
                "samples": samples,
                "disparate_impact_threshold": 0.4,
                "max_difference": 0.6,
            },
        )
        assert gate.main(["--datasets", str(tmp_path)]) == 0

    def test_one_failing_dataset_fails_the_run(self, tmp_path):
        _write(tmp_path, "ok", {"name": "ok", "samples": _balanced()})
        bad = [{"group": "a", "prediction": True} for _ in range(10)]
        bad += [{"group": "b", "prediction": False} for _ in range(10)]
        _write(tmp_path, "bad", {"name": "bad", "samples": bad})
        assert gate.main(["--datasets", str(tmp_path)]) == 1


class TestEmptyDirectory:
    def test_no_datasets_fails_by_default(self, tmp_path, capsys):
        assert gate.main(["--datasets", str(tmp_path)]) == 1
        assert "cannot pass with nothing to examine" in capsys.readouterr().out

    def test_allow_empty_passes_but_says_so(self, tmp_path, capsys):
        assert gate.main(["--datasets", str(tmp_path), "--allow-empty"]) == 0
        assert "NOT performed" in capsys.readouterr().out


class TestDatasetValidation:
    def test_invalid_json_fails(self, tmp_path):
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        assert gate.main(["--datasets", str(tmp_path)]) == 1

    def test_missing_samples_fails(self, tmp_path):
        _write(tmp_path, "empty", {"name": "empty", "samples": []})
        assert gate.main(["--datasets", str(tmp_path)]) == 1

    def test_sample_missing_the_protected_attribute_fails(self, tmp_path):
        _write(tmp_path, "bad", {"name": "bad", "samples": [{"prediction": True}]})
        with pytest.raises(gate.FairnessDatasetError):
            gate.load_dataset(tmp_path / "bad.json")

    def test_custom_protected_attribute_is_honoured(self, tmp_path):
        samples = [
            {"cohort": "x", "prediction": True},
            {"cohort": "y", "prediction": True},
        ]
        _write(
            tmp_path,
            "cohorts",
            {"name": "cohorts", "protected_attribute": "cohort", "samples": samples},
        )
        assert gate.main(["--datasets", str(tmp_path)]) == 0

    def test_labels_are_optional(self, tmp_path):
        samples = [
            {"group": "a", "prediction": True},
            {"group": "b", "prediction": True},
        ]
        result = gate.evaluate_dataset({"name": "n", "samples": samples})
        assert result["passed"] is True
        assert result["equalized_odds_difference"] == 0.0


class TestReport:
    def test_report_file_is_written(self, tmp_path):
        _write(tmp_path, "ok", {"name": "ok", "samples": _balanced()})
        report = tmp_path / "report.json"
        gate.main(["--datasets", str(tmp_path), "--report", str(report)])
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["datasets"][0]["name"] == "ok"
        # Per-group detail is what supports the "measures to detect" half of
        # Art. 10(2)(g) — an aggregate score alone would not.
        assert len(payload["datasets"][0]["groups"]) == 2
