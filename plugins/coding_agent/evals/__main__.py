"""Run the benchmark with the configured model and sandbox; prints a table and the fix rate.

Usage (from the repository root, with the deployment's environment loaded)::

    python -m plugins.coding_agent.evals [case ...]
    python -m plugins.coding_agent.evals --workspace [case ...]

``--workspace`` runs the repository cases (a fix in one file of a small workspace).
"""

from __future__ import annotations

import asyncio
import sys

from ..agent import CodingAgent
from .cases import CASES
from .runner import run_eval, run_workspace_eval
from .workspace_cases import WORKSPACE_CASES


def main(argv: list[str]) -> int:
    """Run the named cases (all when none are named); exit 0 when every case was fixed."""
    workspace = bool(argv) and argv[0] == "--workspace"
    wanted = set(argv[1:] if workspace else argv)
    known = [c.name for c in (WORKSPACE_CASES if workspace else CASES)]
    if wanted - set(known):
        print(f"no such case; known: {', '.join(known)}")
        return 2
    agent = CodingAgent()
    if workspace:
        chosen = [c for c in WORKSPACE_CASES if not wanted or c.name in wanted]
        report = asyncio.run(run_workspace_eval(agent, chosen))
    else:
        cases = [c for c in CASES if not wanted or c.name in wanted]
        report = asyncio.run(run_eval(agent, cases))
    print(report.render())
    return 0 if report.fix_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
