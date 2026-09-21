"""Guards for the packaging, release and CI surface.

Every assertion here stands in for a failure that is invisible until it is
expensive: a wheel that ships in-repo test fixtures, a release that bumps the
framework version but leaves the security policy and the client SDKs behind, a
workflow job that runs with a write token it does not need, or an image that
carries the database-reset scripts into production.

All of it is read from the files themselves — no network, no build, no
subprocess — so the suite stays fast and these stay honest.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

from core._version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
RELEASERC = REPO_ROOT / ".releaserc"
DOCKERFILE = REPO_ROOT / "Dockerfile"
COMPOSE = REPO_ROOT / "compose.yaml"
PYTEST_INI = REPO_ROOT / "pytest.ini"
SECURITY = REPO_ROOT / "SECURITY.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI_WORKFLOW = WORKFLOWS / "ci.yml"
PRE_COMMIT = REPO_ROOT / ".pre-commit-config.yaml"

# Files that must carry this release's version number. `.releaserc`'s prepareCmd
# rewrites every one of them on the runner, which is what makes the published
# artifacts carry the right number — but `main` is pull-request-only, so nothing
# commits those rewrites back. The tree has to arrive at `main` already bumped,
# and `test_every_version_bearing_file_matches_the_source_of_truth` is what
# says so.
VERSION_BEARING_FILES = (
    "core/_version.py",
    "deploy/helm/baselithcore/Chart.yaml",
    "SECURITY.md",
    "sdk/python/baselith_sdk/version.py",
    "sdk/typescript/package.json",
    "sdk/typescript/src/client.ts",
    # info.version in the OpenAPI document; the openapi_drift gate regenerates
    # it from core/_version.py, so a release that skips it reddens the next PR.
    "sdk/openapi.json",
    "mkdocs-site/docs/api/specs/openapi.json",
)

# Where the full version sits inside each of them. SECURITY.md is absent on
# purpose: its table carries the minor only, and
# `test_security_policy_supports_the_current_minor` is what checks it.
VERSION_PATTERNS = {
    "core/_version.py": r'^__version__ = "([^"]+)"$',
    "deploy/helm/baselithcore/Chart.yaml": r'^appVersion: "([^"]+)"$',
    "sdk/python/baselith_sdk/version.py": r'^__version__ = "([^"]+)"$',
    "sdk/typescript/src/client.ts": r"^const VERSION = '([^']+)';$",
}
JSON_VERSION_KEYS = {
    "sdk/typescript/package.json": ("version",),
    "sdk/openapi.json": ("info", "version"),
    "mkdocs-site/docs/api/specs/openapi.json": ("info", "version"),
}


def _pyproject() -> dict[str, Any]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _releaserc() -> dict[str, Any]:
    return json.loads(RELEASERC.read_text(encoding="utf-8"))


def _plugin_config(data: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the options object of a semantic-release plugin entry."""
    for entry in data["plugins"]:
        if isinstance(entry, list) and entry[0] == name:
            return entry[1]
    raise AssertionError(f"{name} is not configured in .releaserc")


def _workflow(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _without_comments(text: str) -> str:
    """Strip ``#`` comment lines, so prose about a pattern is not read as it.

    Both the Dockerfile and the workflows explain at length why they do what
    they do, and those explanations naturally quote the very strings these
    assertions search for.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the `on:` block.

    YAML 1.1 resolves the bare key ``on`` to the boolean ``True``, which is why
    this is not simply ``workflow["on"]``.
    """
    return workflow.get("on", workflow.get(True, {}))


# ---------------------------------------------------------------------------
# Distribution
# ---------------------------------------------------------------------------


def test_fixture_plugins_are_excluded_from_the_distribution() -> None:
    """`include = ["plugins*"]` swept the test fixtures into every install."""
    exclude = _pyproject()["tool"]["setuptools"]["packages"]["find"]["exclude"]
    for fixture in ("plugins.example-plugin", "plugins.test-project"):
        assert fixture in exclude, (
            f"{fixture} is no longer excluded from the wheel. It is an in-repo "
            "fixture, not product; scripts/check_distribution_artifacts.py "
            "asserts the same thing on the built artifact."
        )
        assert f"{fixture}.*" in exclude, (
            f"{fixture} is excluded but its subpackages are not."
        )


def test_source_maps_are_excluded_from_the_wheel() -> None:
    """`sourcemap: 'hidden'` still writes .map files; they must not ship."""
    excluded = _pyproject()["tool"]["setuptools"]["exclude-package-data"]["*"]
    assert "ui/dist/**/*.map" in excluded, (
        "The UI bundle's source maps would ship in the wheel, tripling the "
        "packaged dashboard and exposing its TypeScript sources. Nothing can "
        "even request them — 'hidden' strips the sourceMappingURL comment."
    )


def test_fixture_plugins_are_excluded_from_the_container_image() -> None:
    """The wheel exclusion does not cover `COPY plugins/ plugins/`."""
    ignored = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for fixture in ("plugins/example-plugin/", "plugins/test-project/"):
        assert fixture in ignored, (
            f"{fixture} reaches the container image. The released image copies "
            "plugins/ verbatim, so excluding it from the wheel is not enough."
        )


def test_example_plugin_is_not_enabled_by_default() -> None:
    """Enabled, the reference scaffold loads into the production gateway."""
    config = yaml.safe_load(
        (REPO_ROOT / "configs" / "plugins.yaml").read_text(encoding="utf-8")
    )
    assert config["example-plugin"]["enabled"] is False, (
        "configs/plugins.yaml is copied into the released image, so enabling "
        "the authoring-guide scaffold registers its demo handlers and router "
        "into the production API gateway."
    )


def test_classifiers_cover_every_python_the_matrix_tests() -> None:
    """A trove list that stops early under-reports what is actually supported."""
    classifiers = _pyproject()["project"]["classifiers"]
    matrix = _workflow(CI_WORKFLOW)["jobs"]["python_test"]["strategy"]["matrix"]
    tested = set(matrix["python-version"]) | {
        entry["python-version"] for entry in matrix.get("include", [])
    }
    for version in tested:
        expected = f"Programming Language :: Python :: {version}"
        assert expected in classifiers, (
            f"CI runs the suite on Python {version} but {expected!r} is not a "
            "classifier, so PyPI does not advertise it."
        )


def test_typed_classifier_is_backed_by_a_py_typed_marker() -> None:
    """`Typing :: Typed` is a claim; py.typed is what makes it true."""
    classifiers = _pyproject()["project"]["classifiers"]
    assert "Typing :: Typed" in classifiers
    assert (REPO_ROOT / "core" / "py.typed").is_file()
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]["*"]
    assert "py.typed" in package_data, (
        "py.typed exists but is not declared as package data, so the wheel "
        "would not ship it and the classifier would be a lie."
    )


# ---------------------------------------------------------------------------
# Release automation
# ---------------------------------------------------------------------------


def test_release_rewrites_every_version_bearing_file() -> None:
    """A file the release forgets silently drifts behind forever."""
    prepare_cmd = _plugin_config(_releaserc(), "@semantic-release/exec")["prepareCmd"]
    for path in VERSION_BEARING_FILES:
        assert path in prepare_cmd, (
            f"{path} carries a version but .releaserc's prepareCmd does not "
            "rewrite it. That is how SECURITY.md ended up three minors stale "
            "and both client SDKs sat at 0.1.0 across thirty releases."
        )


def test_the_release_does_not_try_to_push_to_main() -> None:
    """A release that commits cannot run against a pull-request-only branch."""
    names = {
        entry[0] if isinstance(entry, list) else entry
        for entry in _releaserc()["plugins"]
    }
    assert "@semantic-release/git" not in names, (
        "A repository ruleset makes `main` pull-request-only, and a workflow's "
        "GITHUB_TOKEN can never bypass a ruleset: the plugin's push is refused "
        "with GH013 and the release dies in `prepare` — no tag, no GitHub "
        "Release, no PyPI upload, no image. The bump and the changelog entry "
        "belong to the release-prep pull request instead."
    )


def test_every_version_bearing_file_matches_the_source_of_truth() -> None:
    """Nothing commits the bump any more, so the tree has to arrive with it."""
    assert set(VERSION_PATTERNS) | set(JSON_VERSION_KEYS) | {"SECURITY.md"} == set(
        VERSION_BEARING_FILES
    ), "VERSION_BEARING_FILES and the two lookup tables have drifted apart."

    found: dict[str, str] = {}
    for path, pattern in VERSION_PATTERNS.items():
        match = re.search(
            pattern, (REPO_ROOT / path).read_text(encoding="utf-8"), re.MULTILINE
        )
        assert match is not None, f"{path} no longer has a line matching {pattern!r}"
        found[path] = match.group(1)
    for path, keys in JSON_VERSION_KEYS.items():
        document: Any = json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))
        for key in keys:
            document = document[key]
        found[path] = document

    stale = {path: value for path, value in found.items() if value != __version__}
    assert not stale, (
        f"these files disagree with core/_version.py ({__version__}): {stale}. "
        "Run .releaserc's prepareCmd in the release-prep pull request — the "
        "release job runs it too, but only on the runner, so an unbumped tree "
        "publishes a version it does not itself record."
    )


def test_security_policy_supports_the_current_minor() -> None:
    """The supported table is a promise about which minor still gets fixes."""
    minor = ".".join(__version__.split(".")[:2])
    text = SECURITY.read_text(encoding="utf-8")
    assert f"| {minor}.x" in text, (
        f"SECURITY.md does not list {minor}.x as supported while this release "
        f"is {__version__}. .releaserc rewrites it; bump it by hand if you are "
        "changing the version out of band."
    )
    assert f"| < {minor}" in text


def test_contributing_documents_conventional_commits_as_mandatory() -> None:
    """semantic-release derives the whole release from the commit type."""
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "Conventional Commits" in text
    assert "mandatory" in text.lower()
    release_rules = _plugin_config(_releaserc(), "@semantic-release/commit-analyzer")
    for rule in release_rules["releaseRules"]:
        assert f"`{rule['type']}`" in text, (
            f"commit type {rule['type']!r} has a release rule in .releaserc but "
            "is not documented in CONTRIBUTING.md's allowed-types table."
        )


def test_coverage_gate_is_not_parked_below_the_real_number() -> None:
    """A gate several points low permits a silent regression."""
    text = PYTEST_INI.read_text(encoding="utf-8")
    match = re.search(r"--cov-fail-under=(\d+)", text)
    assert match is not None, "pytest.ini no longer enforces a coverage floor"
    assert int(match.group(1)) >= 78, (
        "The branch-coverage gate is a ratchet: raise it as coverage grows, "
        "never lower it to make a branch pass."
    )


# ---------------------------------------------------------------------------
# Container image
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CI workflows
# ---------------------------------------------------------------------------


VITE_OUTPUT = "plugins/baselithbot/ui/dist/"

# Hooks that REWRITE the files they are given. Any one of them pointed at
# VITE_OUTPUT wedges the `ui_build` drift gate permanently.
CONTENT_REWRITING_HOOKS = ("end-of-file-fixer", "mixed-line-ending", "prettier")


def _hook(hook_id: str) -> dict[str, Any]:
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    for repo in config["repos"]:
        for hook in repo["hooks"]:
            if hook["id"] == hook_id:
                return hook
    raise AssertionError(f"no {hook_id} hook in .pre-commit-config.yaml")


@pytest.mark.parametrize("hook_id", CONTENT_REWRITING_HOOKS)
def test_formatters_keep_out_of_the_vite_output(hook_id: str) -> None:
    """A tree a gate diffs byte for byte cannot also be edited by a formatter."""
    hook = _hook(hook_id)
    pattern = hook.get("exclude", "")
    assert re.search(pattern, VITE_OUTPUT) if pattern else False, (
        f"The {hook_id} hook is not excluded from {VITE_OUTPUT}, which is "
        "vite's output and is compared byte for byte against a clean rebuild "
        "by the ui_build gate. end-of-file-fixer appending one newline to the "
        ".js.map files was enough to report all 30 as drift on every CI run, "
        "with no local rebuild able to clear it: the hook put the newline "
        "back on the way into the commit. Prettier is worse -- it un-minifies "
        "the bundles it formats, so it would ship an un-minified dashboard."
    )


def test_prettier_matches_the_files_it_is_excluded_from() -> None:
    """The exclusion above is only load-bearing while `files:` still selects them."""
    files_pattern = _hook("prettier")["files"]
    # If this stops matching, the exclusion is dead weight and its removal
    # looks harmless -- until `files:` widens again and nothing says why.
    assert re.search(files_pattern, "dist/assets/index-abc123.js"), (
        "prettier's `files:` no longer matches the vite bundles. Re-check "
        "whether the dist exclusion is still needed before dropping it."
    )


def test_typescript_build_caches_are_not_tracked() -> None:
    """`tsc -b` rewrites them on every build, so tracking them dirties the tree."""
    git = shutil.which("git")
    assert git is not None, "git is required to check what is tracked"
    tracked = subprocess.run(
        [git, "ls-files", "plugins/baselithbot/ui"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    ).stdout.split()
    for path in tracked:
        assert not path.endswith(".tsbuildinfo"), (
            f"{path} is a tsc incremental-build cache. It is listed in "
            "plugins/baselithbot/ui/.gitignore and was force-added; tracked, "
            "it is rewritten by every `npm run build` and gets swept into "
            "unrelated commits."
        )
        assert "/.tsbuild-node/" not in path, (
            f"{path} is tsc -b output for the node-side config (tsconfig."
            "node.json's outDir). Same problem as the .tsbuildinfo files, and "
            "it ships nowhere -- the wheel's package-data lists ui/dist only."
        )


def test_every_action_reference_is_pinned_to_a_commit() -> None:
    """A tag is mutable: whoever can move it can run code in our token's job."""
    unpinned: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        workflow = _workflow(path)
        for job_name, job in (workflow.get("jobs") or {}).items():
            refs = [job["uses"]] if "uses" in job else []
            refs += [s["uses"] for s in job.get("steps") or [] if "uses" in s]
            for ref in refs:
                if ref.startswith("./"):
                    continue  # reusable workflow in this repo, at this commit
                if not re.search(r"@[0-9a-f]{40}$", ref):
                    unpinned.append(f"{path.name}:{job_name} -> {ref}")
    assert not unpinned, "action references not pinned to a SHA: " + ", ".join(unpinned)


def test_ci_runs_on_the_merge_queue() -> None:
    """Without this the queue has no check to wait on."""
    assert "merge_group" in _triggers(_workflow(CI_WORKFLOW))


def test_merge_queue_runs_are_never_cancelled() -> None:
    """A cancelled queue check reads as failed and drops the PR."""
    concurrency = _workflow(CI_WORKFLOW)["concurrency"]
    assert "merge_group" in concurrency["cancel-in-progress"]


@pytest.mark.parametrize(
    "job", ["zizmor", "helm_lint", "dependency_review", "ui_build"]
)
def test_new_gates_are_present_and_least_privilege(job: str) -> None:
    """Each new job declares its own read-only permissions and a timeout."""
    spec = _workflow(CI_WORKFLOW)["jobs"][job]
    assert spec["permissions"] == {"contents": "read"}, (
        f"{job} does not run with least privilege."
    )
    assert spec["timeout-minutes"] <= 30


def test_no_job_escalates_permissions_without_declaring_them() -> None:
    """The workflow default is read-only; a writer must say so explicitly."""
    workflow = _workflow(CI_WORKFLOW)
    assert workflow["permissions"] == {"contents": "read"}


def test_new_gates_block_the_release_path() -> None:
    """A gate nothing depends on is advisory, whatever branch protection says."""
    needs = _workflow(CI_WORKFLOW)["jobs"]["python_test"]["needs"]
    for gate in ("zizmor", "helm_lint", "ui_build"):
        assert gate in needs, (
            f"{gate} does not block python_test, so a release can ship past it."
        )
    assert "dependency_review" not in needs, (
        "dependency_review only runs on pull_request; a skipped dependency "
        "blocks the dependent job, so listing it would skip python_test on "
        "every push to main."
    )


def test_graceful_shutdown_default_matches_the_application() -> None:
    """One variable must not mean two drains depending on how it is started."""
    backend = (REPO_ROOT / "backend.py").read_text(encoding="utf-8")
    app_default = re.search(
        r'getenv\(\s*"GRACEFUL_SHUTDOWN_TIMEOUT"\s*,\s*"(\d+)"', backend
    )
    assert app_default is not None
    image_default = re.search(
        r"--timeout-graceful-shutdown \$\{GRACEFUL_SHUTDOWN_TIMEOUT:-(\d+)\}",
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    assert image_default is not None
    assert image_default.group(1) == app_default.group(1), (
        f"The image CMD drains for {image_default.group(1)}s but backend.py "
        f"defaults to {app_default.group(1)}s."
    )


def test_helm_render_and_validation_target_the_same_kubernetes() -> None:
    """Otherwise helm renders for one cluster and kubeconform checks another."""
    text = _without_comments(CI_WORKFLOW.read_text(encoding="utf-8"))
    render = re.search(r"--kube-version (\S+)", text)
    validate = re.search(r"-kubernetes-version (\S+)", text)
    assert render is not None and validate is not None
    assert render.group(1) == validate.group(1)


def test_builds_are_reproducible() -> None:
    """An attested artifact nobody can rebuild attests only to our runner."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    builds = re.findall(r"^\s*[\w-]*\s*python3? -m build", text, flags=re.M)
    assert builds, "ci.yml no longer builds the distribution"
    assert text.count('SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"') == len(
        builds
    ), (
        "Every `python -m build` must be preceded by SOURCE_DATE_EPOCH, or "
        "setuptools stamps the current time into the archive members and the "
        "same commit produces two different artifacts."
    )
