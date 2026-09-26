#!/usr/bin/env python3
"""Decide which CI jobs a run needs — the plan the ``changes`` job publishes.

Every change used to cross the whole pipeline four times: on the pull request
into ``develop``, again on the push that merged it, on the pull request into
``main`` and once more on the push that merged that. The pushes re-verified
trees a pull request had just verified, and every run paid for the jobs whose
inputs it had not touched. On one self-hosted runner that serialises every
job, all of that is queue time.

This script replaces "run everything, always" with three modes:

``scoped``
    A pull request into ``develop``. Only the jobs whose inputs changed run,
    and the suite runs on the oldest supported Python only. A skip here is
    never final: the promotion to ``main`` runs everything.
``full``
    A pull request into ``main``, a merge-queue run, a push to ``main`` that no
    green pull-request run vouches for, or any change to the pipeline itself.
    Every job runs on every supported Python.
``release``
    A push to ``main`` whose tree is byte-identical to the head of a pull
    request whose CI run succeeded. The gates already passed on exactly this
    tree, so only the release path runs.

When anything is uncertain — a diff that cannot be computed, an API call that
fails, a push nobody reviewed — the answer is ``full``. A wrong plan can only
cost time, never a gate.

The classification is pure and covered by ``tests/unit/scripts/test_ci_plan.py``;
``main`` wires it to git, the GitHub API and ``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass

#: Every Python the package claims to support; the ``full`` matrix. The
#: trove classifiers in pyproject.toml are checked against this list.
FULL_PYTHONS: tuple[str, ...] = ("3.12", "3.13")
#: A pull request into develop runs the floor only; the promotion to main
#: runs the rest.
SCOPED_PYTHONS: tuple[str, ...] = ("3.12",)

#: The pipeline's own definition. A change here runs everything, so the new
#: plan is exercised by the run that introduces it.
PIPELINE = re.compile(r"^(\.github/workflows/ci\.yml|scripts/ci_plan\.py)$")

#: Prose nothing executes. A pull request that touches only these skips the
#: suite, the evals and the packaging checks; the docs gates still run. Prompt
#: templates are Markdown too, but live under core/ and are code.
DOCS_ONLY = re.compile(
    r"^("
    r"mkdocs-site/"
    r"|[^/]+\.md$"
    r"|plugins/[^/]+/docs/"
    r"|plugins/[^/]+/(README|CHANGELOG)\.md$"
    r"|sdk/[^/]+/README\.md$"
    r"|\.github/(ISSUE_TEMPLATE/|PULL_REQUEST_TEMPLATE)"
    r"|media/"
    r")"
)

#: Per-job inputs for the jobs whose scope is a single tree.
SCOPES: dict[str, re.Pattern[str]] = {
    "workflows": re.compile(r"^\.github/"),
    "helm": re.compile(r"^deploy/helm/"),
    "ui": re.compile(r"^plugins/baselithbot/ui/"),
    "frontend": re.compile(r"^core/static/frontend/"),
    "sdk": re.compile(r"^sdk/"),
    # What the dependency scanners read: manifests, locks, the image recipe
    # and the accepted-risk register they all honour.
    "supply": re.compile(
        r"(^|/)(pyproject\.toml|uv\.lock|requirements[^/]*\.txt"
        r"|package(-lock)?\.json|Dockerfile[^/]*|\.trivyignore\.yaml)$"
    ),
}

FLAGS: tuple[str, ...] = ("gates", "python", "sbom", "image", *SCOPES)


@dataclass(frozen=True)
class Plan:
    """What one CI run executes."""

    mode: str
    flags: dict[str, bool]
    pythons: tuple[str, ...]
    reason: str

    def outputs(self) -> dict[str, str]:
        """The ``$GITHUB_OUTPUT`` lines, as strings Actions expressions compare."""
        out = {"mode": self.mode, "python_versions": json.dumps(list(self.pythons))}
        out.update({k: "true" if v else "false" for k, v in self.flags.items()})
        return out


def full(reason: str) -> Plan:
    """Every job, every Python."""
    return Plan("full", dict.fromkeys(FLAGS, True), FULL_PYTHONS, reason)


def release(reason: str) -> Plan:
    """Only the release path; every gate already passed on this tree."""
    return Plan("release", dict.fromkeys(FLAGS, False), FULL_PYTHONS, reason)


def scoped(changed: list[str]) -> Plan:
    """Only the jobs whose inputs appear in ``changed``."""
    if not changed:
        return full("empty or unreadable diff")
    if any(PIPELINE.match(path) for path in changed):
        return full("the pipeline itself changed")
    flags = {name: any(p.search(f) for f in changed) for name, p in SCOPES.items()}
    flags["gates"] = True
    flags["python"] = not all(DOCS_ONLY.match(path) for path in changed)
    flags["sbom"] = False
    flags["image"] = True  # image_build narrows itself to the image's inputs
    return Plan("scoped", flags, SCOPED_PYTHONS, f"{len(changed)} changed file(s)")


def decide(
    event: str,
    base_ref: str,
    ref: str,
    changed: list[str] | None = None,
    verified_tree: bool = False,
) -> Plan:
    """Choose the plan for one run.

    Args:
        event: ``github.event_name``.
        base_ref: ``github.base_ref`` (pull requests only).
        ref: ``github.ref``.
        changed: Files the pull request changes, for ``scoped``.
        verified_tree: A push to main whose tree a green pull-request run
            already verified.
    """
    if event == "pull_request" and base_ref == "develop":
        return scoped(changed or [])
    if event == "push" and ref == "refs/heads/main" and verified_tree:
        return release("tree verified by a green pull-request run")
    return full(f"{event} on {base_ref or ref}")


# ---------------------------------------------------------------------------
# I/O: git, the GitHub API, $GITHUB_OUTPUT
# ---------------------------------------------------------------------------


def _run(*argv: str) -> str:
    return subprocess.run(argv, check=True, capture_output=True, text=True).stdout


def changed_files() -> list[str]:
    """Paths a pull request changes: the checkout is its merge commit."""
    try:
        return [
            p
            for p in _run("git", "diff", "--name-only", "HEAD^1", "HEAD").splitlines()
            if p
        ]
    except (subprocess.CalledProcessError, OSError):
        return []


def tree_verified(repo: str, sha: str) -> bool:
    """Whether a green pull-request CI run tested exactly the tree at ``sha``.

    The pushed commit must belong to a merged pull request into main, its tree
    must equal that pull request's head tree, and a pull-request CI run on that
    head must have succeeded in ``full`` mode.
    """
    api = ("gh", "api", "-H", "Accept: application/vnd.github+json")
    try:
        pulls = json.loads(_run(*api, f"repos/{repo}/commits/{sha}/pulls"))
        pr = next(
            (p for p in pulls if p["base"]["ref"] == "main" and p.get("merged_at")),
            None,
        )
        if pr is None:
            print("no merged pull request into main carries this commit")
            return False
        head = pr["head"]["sha"]

        def tree(commit: str) -> str:
            data = json.loads(_run(*api, f"repos/{repo}/git/commits/{commit}"))
            return str(data["tree"]["sha"])

        if tree(sha) != tree(head):
            print(f"tree differs from the head of #{pr['number']}")
            return False
        runs = json.loads(
            _run(
                *api,
                f"repos/{repo}/actions/workflows/ci.yml/runs"
                f"?head_sha={head}&event=pull_request&status=success",
            )
        )["workflow_runs"]

        # A run's `pull_requests` is emptied once the pull request merges, so
        # it cannot say which pull request a run belonged to. What matters is
        # the plan it ran, and only `full` has every Python leg: a green run
        # carrying all of them passed every gate on this tree.
        def ran_full(run_id: int) -> bool:
            jobs = json.loads(
                _run(*api, f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
            )["jobs"]
            passed = {j["name"] for j in jobs if j.get("conclusion") == "success"}
            return all(f"Python Tests ({v})" in passed for v in FULL_PYTHONS)

        if not any(ran_full(run["id"]) for run in runs):
            print(f"no green full CI run for #{pr['number']} at {head[:12]}")
            return False
        print(f"tree verified by the green CI run of #{pr['number']}")
        return True
    except (subprocess.CalledProcessError, OSError, KeyError, ValueError) as exc:
        print(f"verification unavailable ({exc.__class__.__name__}); running in full")
        return False


def main() -> int:
    """Compute the plan from the Actions environment and publish it."""
    event = os.environ.get("EVENT_NAME", "")
    base_ref = os.environ.get("BASE_REF", "")
    ref = os.environ.get("REF", "")
    repo = os.environ.get("REPO", "")
    sha = os.environ.get("SHA", "")

    changed = changed_files() if event == "pull_request" else None
    verified = (
        event == "push"
        and ref == "refs/heads/main"
        and bool(repo and sha)
        and tree_verified(repo, sha)
    )
    plan = decide(event, base_ref, ref, changed, verified)

    print(f"mode={plan.mode} ({plan.reason})")
    for key, value in plan.outputs().items():
        print(f"  {key}={value}")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.writelines(f"{k}={v}\n" for k, v in plan.outputs().items())
    return 0


if __name__ == "__main__":
    sys.exit(main())
