"""Guards for the property that a commit, a push and CI cannot disagree.

Every gate that is fast and hermetic is defined once, in
``.pre-commit-config.yaml``. ``git commit`` runs it over the staged files,
``git push`` and CI run it over the whole tree, and CI runs it by invoking
``pre-commit`` rather than by re-spelling each tool's arguments.

Each assertion below stands in for a way that guarantee was actually lost
before: a hook scoped to the staged files where CI scanned a whole tree, a tool
invoked twice with two argument lists and two skip lists, a stage nobody wired.
None of them is stylistic — drop one and "green locally, red in CI" comes back.

All of it is read from the files themselves: no network, no subprocess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# These need what a hook does not have: a built wheel, and a perf run.
CI_ONLY_SCRIPTS = {"check_distribution_artifacts.py", "check_perf_budget.py"}


def _config() -> dict[str, Any]:
    return yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))


def _hooks() -> list[dict[str, Any]]:
    return [hook for repo in _config()["repos"] for hook in repo["hooks"]]


def _hook(hook_id: str) -> dict[str, Any]:
    for hook in _hooks():
        if hook["id"] == hook_id:
            return hook
    raise AssertionError(f"no {hook_id} hook in .pre-commit-config.yaml")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))


def test_push_runs_the_whole_tree_through_the_same_hooks() -> None:
    """`pre-commit install` must wire the stage that closes the staged-files gap."""
    config = _config()
    assert "pre-push" in config["default_install_hook_types"], (
        "Without the pre-push stage installed, `git push` runs nothing and the "
        "first whole-tree check of the branch happens in CI."
    )
    full_tree = _hook("full-tree")
    assert full_tree["stages"] == ["pre-push"]
    assert "--all-files" in full_tree["entry"]
    assert "--hook-stage pre-commit" in full_tree["entry"], (
        "The inner run must select the pre-commit stage explicitly, or it "
        "selects pre-push and recurses into this hook."
    )


@pytest.mark.parametrize("hook_id", ["mypy-core", "bandit"])
def test_scope_sensitive_gates_never_take_a_file_list(hook_id: str) -> None:
    """A gate whose verdict depends on what it is handed must see everything."""
    hook = _hook(hook_id)
    assert hook.get("pass_filenames") is False, (
        f"{hook_id} would be handed only the staged files at commit time. "
        "mypy given one file cannot see the caller in another module that a "
        "signature change just broke, and bandit's scope decides its findings "
        "— so the hook would pass on exactly the commits CI rejects. This is "
        "the divergence the whole arrangement exists to prevent."
    )


def test_ci_runs_the_hooks_instead_of_reimplementing_them() -> None:
    """A second copy of an argument list is a second definition of the gate."""
    runs = "\n".join(
        line
        for line in CI_WORKFLOW.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    for tool in ("ruff check", "ruff format", "mypy core", "bandit -r"):
        assert tool not in runs, (
            f"ci.yml invokes `{tool}` itself. It must run the hook instead "
            "(`pre-commit run --all-files`), or the two argument lists drift — "
            "which is how CI came to scan four trees in full while the "
            "bandit hook only ever saw the staged files."
        )
    assert "pre-commit run --all-files" in runs, (
        "The quality_gates job no longer runs the hooks."
    )


def test_gates_run_on_the_interpreter_ci_pins() -> None:
    """mypy's answers depend on the Python it runs under."""
    assert _config()["default_language_version"]["python"] == "python3.12"
    job = _workflow()["jobs"]["quality_gates"]
    versions = {
        step.get("with", {}).get("python-version")
        for step in job["steps"]
        if "setup-python" in str(step.get("uses", ""))
    }
    assert versions == {"3.12"}


def test_ci_only_skips_hooks_that_exist_and_says_why() -> None:
    """A SKIP naming a renamed hook silently stops skipping anything."""
    job = _workflow()["jobs"]["quality_gates"]
    skipped = {
        name
        for step in job["steps"]
        for name in str(step.get("env", {}).get("SKIP", "")).split(",")
        if name
    }
    assert skipped, "quality_gates skips nothing; the gitleaks note is stale."
    hook_ids = {hook["id"] for hook in _hooks()}
    assert skipped <= hook_ids, (
        f"SKIP names hooks that do not exist: {sorted(skipped - hook_ids)}"
    )


def test_every_gate_script_is_wired_to_a_hook() -> None:
    """A gate only CI runs is one a developer first meets on a pushed branch."""
    hooked = "\n".join(hook.get("entry", "") for hook in _hooks())
    missing = [
        path.name
        for path in sorted((REPO_ROOT / "scripts").glob("check_*.py"))
        if path.name not in CI_ONLY_SCRIPTS and path.name not in hooked
    ]
    assert not missing, (
        f"gate scripts with no hook: {missing}. Add a hook, or add the script "
        "to CI_ONLY_SCRIPTS above with the reason it cannot run locally."
    )
