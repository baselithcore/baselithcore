"""Run the benchmark with the configured model and sandbox; prints a table and the fix rate.

Usage (from the repository root, with the deployment's environment loaded)::

    python -m plugins.coding_agent.evals [case ...]
"""

from __future__ import annotations

import asyncio
import sys

from ..agent import CodingAgent
from .cases import CASES
from .runner import run_eval


def main(argv: list[str]) -> int:
    """Run the named cases (all when none are named); exit 0 when every case was fixed."""
    wanted = set(argv)
    cases = [c for c in CASES if not wanted or c.name in wanted]
    if not cases:
        print(f"no such case; known: {', '.join(c.name for c in CASES)}")
        return 2
    report = asyncio.run(run_eval(CodingAgent(), cases))
    print(report.render())
    return 0 if report.fix_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
