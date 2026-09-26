"""The CI plan may only ever cost time, never a gate.

`scripts/ci_plan.py` decides which jobs a run executes. Each case below pins a
way it could skip something it must not: a push nobody verified released
without gates, a prompt template mistaken for prose, a pipeline change that
never ran its own new plan, an unreadable diff read as "nothing changed".
"""

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import ci_plan
from scripts.ci_plan import FLAGS, FULL_PYTHONS, SCOPED_PYTHONS, decide

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _develop_pr(*changed: str):
    return decide("pull_request", "develop", "refs/pull/1/merge", list(changed))


def test_promotion_to_main_runs_everything() -> None:
    plan = decide("pull_request", "main", "refs/pull/2/merge", ["README.md"])
    assert plan.mode == "full"
    assert all(plan.flags.values())
    assert plan.pythons == FULL_PYTHONS


@pytest.mark.parametrize("event", ["merge_group", "workflow_dispatch", "schedule"])
def test_anything_but_a_develop_pr_or_verified_push_is_full(event: str) -> None:
    assert decide(event, "", "refs/heads/main").mode == "full"


def test_an_unverified_push_to_main_runs_every_gate() -> None:
    """A bypass push, or one the API could not vouch for, is not trusted."""
    plan = decide("push", "", "refs/heads/main", verified_tree=False)
    assert plan.mode == "full"
    assert all(plan.flags.values())


def test_a_verified_push_to_main_only_releases() -> None:
    plan = decide("push", "", "refs/heads/main", verified_tree=True)
    assert plan.mode == "release"
    assert not any(plan.flags.values())


def test_verification_only_counts_on_main() -> None:
    assert decide("push", "", "refs/heads/develop", verified_tree=True).mode == "full"


def test_an_empty_diff_is_not_an_empty_change() -> None:
    """`git diff` failing must not read as "nothing to check"."""
    assert _develop_pr().mode == "full"


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "scripts/ci_plan.py"])
def test_a_pipeline_change_runs_its_own_new_plan_in_full(path: str) -> None:
    assert _develop_pr(path, "mkdocs-site/docs/index.md").mode == "full"


def test_develop_prs_run_the_floor_python_only() -> None:
    plan = _develop_pr("core/api/factory.py")
    assert plan.mode == "scoped"
    assert plan.pythons == SCOPED_PYTHONS
    assert plan.flags["python"]
    assert plan.flags["gates"]
    assert not plan.flags["sbom"]


@pytest.mark.parametrize(
    "path",
    [
        "mkdocs-site/docs/core-modules/db.md",
        "README.md",
        "plugins/baselithbot/docs/security.md",
        "plugins/web_scraper/README.md",
    ],
)
def test_prose_only_skips_the_suite(path: str) -> None:
    plan = _develop_pr(path)
    assert not plan.flags["python"]
    assert plan.flags["gates"]


@pytest.mark.parametrize(
    "path",
    [
        "core/chat/prompts/conversation_system.md",  # a prompt template is code
        "plugins/baselithbot/SKILL.md",
        "evals/cases/qa.yaml",
        "configs/.env.base",
        "deploy/helm/baselithcore/values.yaml",  # tests render the chart
    ],
)
def test_executed_markdown_and_data_still_run_the_suite(path: str) -> None:
    assert _develop_pr("README.md", path).flags["python"]


@pytest.mark.parametrize(
    ("path", "flag"),
    [
        ("deploy/helm/baselithcore/templates/deployment.yaml", "helm"),
        ("plugins/baselithbot/ui/src/App.tsx", "ui"),
        ("core/static/frontend/js/api.js", "frontend"),
        ("sdk/typescript/src/client.ts", "sdk"),
        (".github/workflows/codeql.yml", "workflows"),
        ("uv.lock", "supply"),
        ("plugins/baselithbot/ui/package-lock.json", "supply"),
        ("Dockerfile", "supply"),
        (".trivyignore.yaml", "supply"),
    ],
)
def test_single_tree_jobs_run_when_their_tree_changes(path: str, flag: str) -> None:
    assert _develop_pr(path).flags[flag]
    assert not _develop_pr("core/api/factory.py").flags[flag]


def test_outputs_cover_every_flag_the_workflow_reads() -> None:
    """A flag the workflow compares against but the plan never sets reads as
    the empty string, which is never 'true' — a silent skip."""
    outputs = _develop_pr("core/api/factory.py").outputs()
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    read = set(re.findall(r"needs\.changes\.outputs\.(\w+)", text))
    declared = set(yaml.safe_load(text)["jobs"]["changes"]["outputs"])
    assert read <= declared, f"read but not declared: {sorted(read - declared)}"
    assert declared <= set(outputs), (
        f"declared, never set: {sorted(declared - set(outputs))}"
    )
    assert set(FLAGS) <= set(outputs)


# ---------------------------------------------------------------------------
# tree_verified: the only door into `release` mode
# ---------------------------------------------------------------------------

PR = {
    "number": 7,
    "base": {"ref": "main"},
    "merged_at": "2026-09-26T00:00:00Z",
    "head": {"sha": "head"},
}
FULL_JOBS = [
    {"name": f"Python Tests ({v})", "conclusion": "success"} for v in FULL_PYTHONS
]


def _api(pulls=(PR,), trees=None, runs=({"id": 1},), jobs=FULL_JOBS):
    trees = trees or {"merge": "T", "head": "T"}

    def fake(*argv: str) -> str:
        path = argv[-1]
        if path.endswith("/pulls"):
            return json.dumps(list(pulls))
        if "/git/commits/" in path:
            return json.dumps({"tree": {"sha": trees[path.rsplit("/", 1)[1]]}})
        if "/workflows/ci.yml/runs" in path:
            return json.dumps({"workflow_runs": list(runs)})
        if "/jobs" in path:
            return json.dumps({"jobs": list(jobs)})
        raise AssertionError(path)

    return fake


def _verified(monkeypatch: pytest.MonkeyPatch, **kw) -> bool:
    monkeypatch.setattr(ci_plan, "_run", _api(**kw))
    return ci_plan.tree_verified("o/r", "merge")


def test_a_merge_of_a_fully_verified_tree_releases(monkeypatch) -> None:
    assert _verified(monkeypatch)


def test_a_push_no_pull_request_carries_is_not_verified(monkeypatch) -> None:
    assert not _verified(monkeypatch, pulls=())


def test_a_pull_request_into_develop_does_not_count(monkeypatch) -> None:
    assert not _verified(monkeypatch, pulls=({**PR, "base": {"ref": "develop"}},))


def test_a_tree_the_run_did_not_test_is_not_verified(monkeypatch) -> None:
    assert not _verified(monkeypatch, trees={"merge": "T1", "head": "T2"})


def test_a_scoped_run_is_not_a_verification(monkeypatch) -> None:
    """A develop run on the same head skipped gates; it vouches for nothing."""
    scoped_jobs = [
        {"name": f"Python Tests ({SCOPED_PYTHONS[0]})", "conclusion": "success"}
    ]
    assert not _verified(monkeypatch, jobs=scoped_jobs)


def test_no_green_run_is_not_verified(monkeypatch) -> None:
    assert not _verified(monkeypatch, runs=())


def test_an_api_failure_falls_back_to_full(monkeypatch) -> None:
    def broken(*argv: str) -> str:
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(ci_plan, "_run", broken)
    assert not ci_plan.tree_verified("o/r", "merge")
