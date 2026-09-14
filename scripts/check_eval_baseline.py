#!/usr/bin/env python3
"""Eval-corpus ratchet: the eval suites may grow, never silently change.

The CI quality gates are only as strong as their corpora — a deleted
red-team case or a trimmed regression suite weakens the gate without any
test failing. This script freezes the current per-suite case counts in
``evals/baseline.json`` (the same ratchet pattern as
``scripts/check_file_size.py``): a run fails when any suite has fewer cases
than its baselined count. Growing a suite is always allowed; after growing
it, refresh the baseline with ``--update-baseline`` so the new floor sticks.

Counts alone, however, only catch *deletion*. Editing a case in place — an
assertion relaxed, an expected keyword removed, an adversarial prompt
defanged — keeps the count identical and weakens the gate just as much. The
baseline therefore also carries ``dataset_sha256``, a hash over every corpus
file's path and contents, and ``dataset_version``, an integer bumped each
time that hash changes. A corpus edit that does not come with a refreshed
baseline fails the gate, so the change has to be declared in the diff.

Usage:
    python scripts/check_eval_baseline.py
    python scripts/check_eval_baseline.py --update-baseline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALS_DIR = REPO_ROOT / "evals"
BASELINE_FILE = EVALS_DIR / "baseline.json"

#: Suites under evals/ counted by the ratchet: directory -> glob of corpus
#: files. YAML files must hold a top-level list of cases; JSON a list of runs.
_SUITES: dict[str, str] = {
    "cases": "*.yaml",
    "red_team": "*.yaml",
    "runs": "*.json",
}

#: Baseline keys that carry metadata rather than a per-suite case count.
DATASET_HASH_KEY = "dataset_sha256"
DATASET_VERSION_KEY = "dataset_version"
_METADATA_KEYS = frozenset({DATASET_HASH_KEY, DATASET_VERSION_KEY})


def dataset_sha256(evals_dir: Path) -> str:
    """Content hash over every ratcheted corpus file, path included.

    The digest folds in each file's suite-relative path before its bytes, so
    renaming, splitting or merging corpus files registers as drift just like
    editing one. Files are visited in sorted order, making the hash
    reproducible across filesystems.
    """
    digest = hashlib.sha256()
    for suite, pattern in sorted(_SUITES.items()):
        suite_dir = evals_dir / suite
        if not suite_dir.is_dir():
            continue
        for file in sorted(suite_dir.glob(pattern)):
            digest.update(f"{suite}/{file.name}\0".encode())
            digest.update(file.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _count_file(path: Path) -> int:
    """Number of corpus entries in one file (0 for malformed/non-list)."""
    try:
        if path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(data) if isinstance(data, list) else 0


def count_suites(evals_dir: Path) -> dict[str, int]:
    """Per-suite case counts for every ratcheted suite directory."""
    counts: dict[str, int] = {}
    for suite, pattern in _SUITES.items():
        suite_dir = evals_dir / suite
        total = 0
        if suite_dir.is_dir():
            for file in sorted(suite_dir.glob(pattern)):
                total += _count_file(file)
        counts[suite] = total
    return counts


def load_baseline_document(baseline_file: Path) -> dict[str, Any]:
    """The committed baseline verbatim, metadata included ({} when absent)."""
    try:
        data = json.loads(baseline_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def load_baseline(baseline_file: Path) -> dict[str, int]:
    """The committed per-suite floors, without the metadata keys."""
    document = load_baseline_document(baseline_file)
    return {k: int(v) for k, v in document.items() if k not in _METADATA_KEYS}


def check_eval_baseline(evals_dir: Path, baseline: dict[str, Any]) -> list[str]:
    """Violations: a suite below its floor, or a drifted corpus hash.

    Accepts either the full baseline document or the counts-only mapping; a
    baseline written before ``dataset_sha256`` existed simply skips the
    content check.
    """
    counts = count_suites(evals_dir)
    violations: list[str] = []
    for suite, floor in baseline.items():
        if suite in _METADATA_KEYS:
            continue
        current = counts.get(suite, 0)
        if current < int(floor):
            violations.append(
                f"evals/{suite}: {current} case(s), baseline floor is {floor} "
                "— eval corpora are a ratchet: restore the deleted cases or "
                "replace them with equivalents"
            )

    expected_hash = baseline.get(DATASET_HASH_KEY)
    if expected_hash:
        actual = dataset_sha256(evals_dir)
        if actual != expected_hash:
            violations.append(
                f"evals: dataset_sha256 drifted ({actual[:12]}… vs baselined "
                f"{str(expected_hash)[:12]}…) — a corpus file was edited, "
                "renamed or added. Review the change, then re-stamp the "
                "baseline with: python scripts/check_eval_baseline.py "
                "--update-baseline"
            )
    return violations


def write_baseline(evals_dir: Path, baseline_file: Path) -> dict[str, Any]:
    """Freeze the current counts, corpus hash and dataset version.

    ``dataset_version`` increments only when the corpus hash actually
    changed, so re-stamping an unchanged baseline is a no-op.
    """
    previous = load_baseline_document(baseline_file)
    digest = dataset_sha256(evals_dir)
    version = int(previous.get(DATASET_VERSION_KEY, 0) or 0)
    if not previous or previous.get(DATASET_HASH_KEY) != digest:
        version += 1
    document: dict[str, Any] = dict(count_suites(evals_dir))
    document[DATASET_HASH_KEY] = digest
    document[DATASET_VERSION_KEY] = version
    baseline_file.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Freeze the current suite counts as the new floor.",
    )
    args = parser.parse_args()

    if args.update_baseline:
        document = write_baseline(EVALS_DIR, BASELINE_FILE)
        print(f"Baseline updated: {document}")
        return 0

    baseline = load_baseline_document(BASELINE_FILE)
    if not baseline:
        print(
            "No evals/baseline.json found — create one with --update-baseline.",
            file=sys.stderr,
        )
        return 1
    violations = check_eval_baseline(EVALS_DIR, baseline)
    for line in violations:
        print(f"ERROR: {line}", file=sys.stderr)
    if not violations:
        print("Eval corpus ratchet OK.")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
