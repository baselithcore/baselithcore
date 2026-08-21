#!/usr/bin/env python3
"""PostToolUse gate runner: surface repo gate failures at edit time.

Runs only the cheap, always-relevant gates (ruff on the touched file, the
500-line ratchet, and the Sacred Core boundary check when `core/` was touched).
The expensive gates — mypy, the two strict typing checks, pytest — stay out of
the edit loop; the `run-gates` procedure covers them before a PR.

Exit code 2 hands stderr back to the model as feedback to act on.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LINTED_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".vue"})
MAX_REPORTED_LINES = 25


def _run(command: list[str]) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    raw_path = payload.get("tool_input", {}).get("file_path")
    if not raw_path:
        return

    path = Path(raw_path)
    if path.suffix not in LINTED_SUFFIXES or not path.exists():
        return

    try:
        relative = path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return

    failures: list[str] = []

    if path.suffix == ".py":
        _run([sys.executable, "-m", "ruff", "check", "--fix", "--quiet", relative])
        _run([sys.executable, "-m", "ruff", "format", "--quiet", relative])
        code, output = _run([sys.executable, "-m", "ruff", "check", relative])
        if code != 0 and output:
            failures.append(output)

    code, output = _run([sys.executable, "scripts/check_file_size.py"])
    if code != 0 and output:
        failures.append(output)

    if relative.startswith("core/"):
        code, output = _run(
            [sys.executable, "scripts/check_architecture_boundaries.py"]
        )
        if code != 0 and output:
            failures.append(output)

    if not failures:
        return

    report = "\n".join(failures).splitlines()[:MAX_REPORTED_LINES]
    print(
        "Repo gates failed after editing "
        f"{relative}. Fix these before moving on "
        "(never baseline an over-cap file, never extend the legacy allowlist):"
        "\n" + "\n".join(report),
        file=sys.stderr,
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
