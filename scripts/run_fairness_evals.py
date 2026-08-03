#!/usr/bin/env python3
"""Deterministic bias-examination gate (EU AI Act Art. 10(2)(f)/(g), Art. 15).

Art. 10(2)(f)/(g) requires training, validation and testing data sets for
high-risk AI systems to be examined in view of possible biases, together with
appropriate measures to detect them. An examination performed once at model
selection and never again is not that. This gate turns it into a merge check:
group fairness metrics are computed over the labelled datasets in
``evals/fairness/`` and the job fails when a configured threshold is breached.

No LLM is invoked — the gate is deterministic and CI-safe (no API keys, no cost,
no network), exactly like the trajectory eval gate.

Dataset format (JSON), one file per dataset::

    {
      "name": "loan-scoring-holdout",
      "protected_attribute": "group",
      "disparate_impact_threshold": 0.8,
      "max_difference": 0.1,
      "samples": [{"group": "a", "prediction": true, "label": true}, ...]
    }

``label`` is optional; without it only the label-free metrics (selection rate,
demographic parity, disparate impact) are meaningful.

An **empty dataset directory fails the gate**. That is deliberate: a bias
examination that silently examines nothing is the failure mode this exists to
prevent. Delete the shipped example only when replacing it with your own.

Usage:
    python scripts/run_fairness_evals.py [--datasets evals/fairness] \
        [--report report.json] [--allow-empty]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.evaluation.fairness import evaluate_fairness  # noqa: E402


class FairnessDatasetError(ValueError):
    """Raised when a dataset file cannot be read as a fairness dataset."""


def load_dataset(path: Path) -> dict[str, Any]:
    """Load and validate one dataset file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FairnessDatasetError(f"{path.name}: invalid JSON — {exc}") from exc
    if not isinstance(data, dict):
        raise FairnessDatasetError(f"{path.name}: top level must be an object")
    samples = data.get("samples")
    if not isinstance(samples, list) or not samples:
        raise FairnessDatasetError(f"{path.name}: 'samples' must be a non-empty list")
    attribute = data.get("protected_attribute", "group")
    for index, sample in enumerate(samples):
        if attribute not in sample or "prediction" not in sample:
            raise FairnessDatasetError(
                f"{path.name}: sample {index} needs '{attribute}' and 'prediction'"
            )
    return data


def evaluate_dataset(data: dict[str, Any]) -> dict[str, Any]:
    """Compute the fairness report for one loaded dataset."""
    attribute = data.get("protected_attribute", "group")
    samples = data["samples"]
    groups = [str(s[attribute]) for s in samples]
    predictions = [bool(s["prediction"]) for s in samples]
    labels = (
        [bool(s["label"]) for s in samples]
        if all("label" in s for s in samples)
        else None
    )
    report = evaluate_fairness(
        groups,
        predictions,
        labels,
        disparate_impact_threshold=data.get("disparate_impact_threshold", 0.8),
        max_difference=data.get("max_difference", 0.1),
    )
    return {"name": data.get("name", "unnamed"), **report.to_dict()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets",
        default=str(REPO_ROOT / "evals" / "fairness"),
        help="Directory of JSON fairness datasets",
    )
    parser.add_argument("--report", default=None, help="Write a JSON report here")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Pass when no dataset is present instead of failing (not for CI)",
    )
    args = parser.parse_args(argv)

    directory = Path(args.datasets)
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        message = f"No fairness datasets found in {directory}"
        if args.allow_empty:
            print(f"⚠️  {message} — bias examination NOT performed.")
            return 0
        print(
            f"❌ {message}. The Art. 10(2)(f)/(g) bias examination cannot pass "
            "with nothing to examine; add a dataset or pass --allow-empty."
        )
        return 1

    results: list[dict[str, Any]] = []
    failed = 0
    for path in files:
        try:
            result = evaluate_dataset(load_dataset(path))
        except FairnessDatasetError as exc:
            print(f"❌ {exc}")
            return 1
        results.append(result)
        if result["passed"]:
            print(
                f"✅ {result['name']}: disparate impact "
                f"{result['disparate_impact_ratio']:.3f}, parity gap "
                f"{result['demographic_parity_difference']:.3f}"
            )
        else:
            failed += 1
            print(f"❌ {result['name']}:")
            for violation in result["violations"]:
                print(f"   - {violation}")

    if args.report:
        Path(args.report).write_text(
            json.dumps({"datasets": results}, indent=2), encoding="utf-8"
        )

    print(f"\n{len(results) - failed}/{len(results)} fairness datasets passed.")
    if failed:
        print(
            "Fairness thresholds are a detection aid, not a definition of "
            "fairness — review the per-group breakdown in the report before "
            "adjusting a threshold to make this green."
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
