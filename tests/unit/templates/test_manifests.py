"""Every dependency manifest checked in under ``templates/`` is valid as is.

GitHub's dependency graph parses each ``requirements*.txt`` and
``pyproject.toml`` in the repository. The starters used to ship
``baselith-core>={framework_version}`` — a placeholder ``baselith init``
renders, but not a PEP 508 requirement — and the auto-submission failed with
``dependency_file_not_evaluatable``. Placeholder-bearing manifests are kept as
``*.tmpl`` sources instead; whatever keeps a manifest's name must parse.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement

pytestmark = [pytest.mark.unit]

TEMPLATES_PATH = Path(__file__).resolve().parents[3] / "templates"
_PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")


def _manifests(pattern: str) -> list[Path]:
    return sorted(
        p
        for p in TEMPLATES_PATH.rglob(pattern)
        if "node_modules" not in p.parts and p.is_file()
    )


def _requirement_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith(("#", "-")):
            lines.append(line)
    return lines


def test_the_scan_finds_the_manifests() -> None:
    assert _manifests("requirements*.txt"), "no requirements file found"


@pytest.mark.parametrize(
    "path",
    _manifests("requirements*.txt"),
    ids=lambda p: p.relative_to(TEMPLATES_PATH).as_posix(),
)
def test_requirements_files_parse(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not _PLACEHOLDER.search(text), (
        f"{path}: unrendered placeholder — rename it to "
        f"{path.name}.tmpl so `baselith init` renders it"
    )
    for line in _requirement_lines(path):
        Requirement(line)  # raises InvalidRequirement


@pytest.mark.parametrize(
    "path",
    _manifests("pyproject.toml"),
    ids=lambda p: p.relative_to(TEMPLATES_PATH).as_posix(),
)
def test_pyproject_files_parse(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not _PLACEHOLDER.search(text), f"{path}: unrendered placeholder"
    project = tomllib.loads(text).get("project", {})
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs.extend(extra)
    for spec in specs:
        Requirement(spec)
