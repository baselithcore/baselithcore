"""Repeatable, service-free checks for the Core installation workflow.

Run with the development Python environment. This is a focused regression gate,
not a replacement for the full CI suite or the final clean Docker test.

Named ``run_`` rather than ``check_`` on purpose: it defines no gate of its own,
it *invokes* four that pre-commit already owns plus a pytest selection. Every
``scripts/check_*.py`` is required to be wired to a hook
(``tests/unit/test_quality_gates_wiring.py``) so that a developer never first
meets a gate on a pushed branch — a runner that shells out to pytest is exactly
what must not become a commit hook.
"""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    commands = [
        [sys.executable, f"scripts/{script}.py"]
        for script in (
            "check_architecture_boundaries",
            "check_file_size",
            "check_exception_hygiene",
            "check_public_api",
        )
    ]
    commands.append(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--no-cov",
            "tests/unit/core/cli",
            "tests/unit/core/test_docker_plugin_workflow.py",
            "tests/unit/core/test_doc_sources_fallback.py",
            "tests/unit/core/test_plugin_installation_contract.py",
            "tests/unit/test_plugin_versioning.py",
        ]
    )
    for command in commands:
        print("Running: " + " ".join(command[1:]), flush=True)
        result = subprocess.run(command, cwd=root, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
