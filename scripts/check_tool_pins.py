#!/usr/bin/env python3
"""Tool-version pins must agree across the files that declare them.

Most gates are single-sourced in ``.pre-commit-config.yaml``: CI runs the
hooks, so there is only one version of ruff, mypy or bandit in play and nothing
to compare. A few pins are duplicated on purpose, and those are what this gate
watches:

``ruff`` / ``mypy``
    Also named in pyproject's ``dev`` extra, so ``pip install -e ".[dev]"``
    gives a toolchain that agrees with the hooks when a developer runs ``ruff
    check`` or ``mypy`` by hand.

``gitleaks``
    Also named in ``.github/workflows/ci.yml``: the hook scans the staged
    change, the CI job scans the full history, and a full-history scan cannot
    run from a hook. Two invocations, one scanner — they must be the same
    scanner or the two disagree about what a secret looks like.

``pre-commit``
    Pinned in the workflow that runs the hooks, and floor-specified in the
    ``dev`` extra. The pin has to satisfy both that floor and the
    ``minimum_pre_commit_version`` the config declares, or CI runs the hooks
    with a pre-commit that reads the stage names differently from the one on
    the developer's machine.

The repository used to ask for this in comments ("keep in lockstep with ..."),
which is documentation, not a mechanism: the mypy hook sat on 1.11 while CI
gated on 2.3 and nothing noticed. Everything here is a plain text or TOML read
— no PyYAML, so the hook can be ``language: system``.

Exit status:
    0 — every duplicated pin agrees.
    1 — at least one disagreement, each reported with both locations.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRECOMMIT = ROOT / ".pre-commit-config.yaml"
PYPROJECT = ROOT / "pyproject.toml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


# --------------------------------------------------------------------------
# readers
# --------------------------------------------------------------------------
def _precommit_rev(text: str, repo_url: str) -> str | None:
    """The ``rev:`` of the ``- repo: <repo_url>`` block, without a leading v."""
    pattern = re.compile(
        rf"^\s*-\s*repo:\s*{re.escape(repo_url)}\s*$\n(?:.*\n)*?\s*rev:\s*v?([^\s#]+)",
        re.MULTILINE,
    )
    match = pattern.search(text)
    return match.group(1) if match else None


def _precommit_dep(text: str, package: str) -> set[str]:
    """Every ``- <package>==<version>`` pinned in an additional_dependencies."""
    pattern = re.compile(rf"^\s*-\s*{re.escape(package)}==([^\s#]+)", re.MULTILINE)
    return set(pattern.findall(text))


def _precommit_minimum(text: str) -> str | None:
    match = re.search(
        r"^minimum_pre_commit_version:\s*'?\"?([0-9.]+)", text, re.MULTILINE
    )
    return match.group(1) if match else None


def _dev_extra_pins(data: dict[str, object]) -> dict[str, str]:
    """Exactly-pinned (``==``) packages in the ``dev`` optional-dependency."""
    optional = data.get("project", {})
    assert isinstance(optional, dict)
    extras = optional.get("optional-dependencies", {})
    assert isinstance(extras, dict)
    dev = extras.get("dev", [])
    pins: dict[str, str] = {}
    for spec in dev:
        match = re.fullmatch(r"([A-Za-z0-9._-]+)==([^\s;]+)", str(spec).strip())
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def _dev_extra_floor(data: dict[str, object], package: str) -> str | None:
    """The ``>=`` floor a ``dev`` requirement declares, if it declares one."""
    optional = data.get("project", {})
    assert isinstance(optional, dict)
    extras = optional.get("optional-dependencies", {})
    assert isinstance(extras, dict)
    for spec in extras.get("dev", []):
        match = re.fullmatch(
            rf"{re.escape(package)}>=([^\s,;]+)", str(spec).strip(), re.IGNORECASE
        )
        if match:
            return match.group(1)
    return None


def _ci_env(text: str, name: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(name)}:\s*v?([^\s#]+)", text, re.MULTILINE)
    return match.group(1) if match else None


def _ci_pip_pin(text: str, package: str) -> set[str]:
    pattern = re.compile(rf"\b{re.escape(package)}==([0-9][^\s'\"]*)")
    return set(pattern.findall(text))


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------
def _check_single(
    problems: list[str],
    label: str,
    left: tuple[str, str | None],
    right: tuple[str, str | None],
) -> None:
    """Two locations must name the same version, and both must name one."""
    (left_where, left_value), (right_where, right_value) = left, right
    if left_value is None:
        problems.append(f"{label}: no pin found in {left_where}")
        return
    if right_value is None:
        problems.append(f"{label}: no pin found in {right_where}")
        return
    if left_value != right_value:
        problems.append(
            f"{label}: {left_where} pins {left_value}, {right_where} pins "
            f"{right_value} — the two disagree about which {label} is the gate"
        )


def main() -> int:
    precommit = PRECOMMIT.read_text(encoding="utf-8")
    ci = CI.read_text(encoding="utf-8")
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev_pins = _dev_extra_pins(pyproject)

    problems: list[str] = []

    # ruff — hook rev vs the `dev` extra a developer installs.
    _check_single(
        problems,
        "ruff",
        (
            ".pre-commit-config.yaml (ruff-pre-commit rev)",
            _precommit_rev(precommit, "https://github.com/astral-sh/ruff-pre-commit"),
        ),
        ("pyproject.toml (dev extra)", dev_pins.get("ruff")),
    )

    # mypy — pinned in the typing hooks' additional_dependencies. More than one
    # value there would mean two hooks type-check with two mypys.
    mypy_hook_pins = _precommit_dep(precommit, "mypy")
    if len(mypy_hook_pins) > 1:
        problems.append(
            "mypy: .pre-commit-config.yaml pins more than one version "
            f"({', '.join(sorted(mypy_hook_pins))}) — the typing hooks would "
            "disagree with each other"
        )
    _check_single(
        problems,
        "mypy",
        (
            ".pre-commit-config.yaml (typing hooks)",
            next(iter(mypy_hook_pins)) if len(mypy_hook_pins) == 1 else None,
        ),
        ("pyproject.toml (dev extra)", dev_pins.get("mypy")),
    )

    # gitleaks — the hook scans the staged change, the CI job the full history.
    # A checkout with neither is not in scope: this gate compares duplicated
    # pins, it does not decide which scanners a repository runs. One without
    # the other is reported, because that is a half-wired scanner.
    gitleaks_hook = _precommit_rev(precommit, "https://github.com/gitleaks/gitleaks")
    gitleaks_ci = _ci_env(ci, "GITLEAKS_VERSION")
    if gitleaks_hook or gitleaks_ci:
        _check_single(
            problems,
            "gitleaks",
            (".pre-commit-config.yaml (gitleaks rev)", gitleaks_hook),
            (".github/workflows/ci.yml (GITLEAKS_VERSION)", gitleaks_ci),
        )

    # pre-commit itself — CI installs it to run the hooks, so its version has to
    # clear both the floor in the dev extra and the config's declared minimum.
    ci_precommit = _ci_pip_pin(ci, "pre-commit")
    if len(ci_precommit) != 1:
        problems.append(
            "pre-commit: expected exactly one `pre-commit==<version>` pin in "
            f".github/workflows/ci.yml, found {len(ci_precommit)}"
        )
    else:
        pinned = next(iter(ci_precommit))
        for where, floor in (
            ("pyproject.toml dev extra", _dev_extra_floor(pyproject, "pre-commit")),
            (".pre-commit-config.yaml", _precommit_minimum(precommit)),
        ):
            if floor and _version_tuple(pinned) < _version_tuple(floor):
                problems.append(
                    f"pre-commit: ci.yml pins {pinned}, below the {floor} floor "
                    f"declared in {where}"
                )

    if problems:
        print("Tool pins disagree across the files that declare them:\n")
        for problem in problems:
            print(f"  ✗ {problem}")
        print(
            "\nEach of these versions is duplicated deliberately (see the "
            "docstring in scripts/check_tool_pins.py). Update every location, "
            "in one commit."
        )
        return 1

    checked = ["ruff", "mypy", "pre-commit"]
    if gitleaks_hook or gitleaks_ci:
        checked.insert(2, "gitleaks")
    print(f"Tool pins agree: {', '.join(checked)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
