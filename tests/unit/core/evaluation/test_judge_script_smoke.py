"""Smoke tests for the scheduled LLM-as-judge eval script.

The judge gate is deliberately NOT part of the merge gate (nondeterministic,
needs provider credentials); it runs on a schedule. These tests pin the
script's contract without invoking any LLM:

* no provider credentials → exit 0 with an explicit "skipped" message (a
  scheduled job on a fork or credential-less runner must not go red);
* ``--dry-run`` validates that cases and recorded runs load.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPO_ROOT / "scripts" / "run_judge_evals.py"


def _run(*args: str, env_overrides: dict[str, str] | None = None):
    import os

    env = {**os.environ, **(env_overrides or {})}
    # Hermetic: no inherited or repo-.env credentials may turn this into a
    # live LLM run. Keys are set to EMPTY (not popped): the config layer
    # loads the project .env with override=False, so a present-but-empty
    # process variable shadows any real key in .env, and env_ignore_empty
    # then treats it as absent.
    for key in ("LLM_API_KEY", "LLM_OPENAI_API_KEY", "OPENAI_API_KEY"):
        env[key] = ""
    env["LLM_PROVIDER"] = "openai"  # keyed provider, key absent → unconfigured
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=120,
    )


def test_without_credentials_skips_with_exit_zero():
    result = _run()
    assert result.returncode == 0, result.stderr
    assert "skip" in (result.stdout + result.stderr).lower()


def test_dry_run_loads_assets_and_exits_zero():
    result = _run("--dry-run")
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "cases" in combined.lower()
