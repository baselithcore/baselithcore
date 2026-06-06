"""Run strict mypy checks on hardened core resilience modules.

This gate keeps pressure on the first stable core modules that have been
cleaned up, without forcing the whole `core/` tree into strict mode at once.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STRICT_CORE_FILES = (
    "core/resilience/circuit_breaker.py",
    "core/resilience/rate_limiter.py",
    "core/resilience/retry.py",
    "core/resilience/distributed_lock.py",
)


def main() -> int:
    """CLI entrypoint."""
    cmd = [
        sys.executable,
        "-m",
        "mypy",
        "--ignore-missing-imports",
        "--follow-imports=skip",
        "--no-error-summary",
        "--warn-unused-ignores",
        "--disallow-untyped-defs",
        *STRICT_CORE_FILES,
    ]
    completed = subprocess.run(cmd, cwd=REPO_ROOT)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
