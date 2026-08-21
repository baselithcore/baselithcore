#!/usr/bin/env python3
"""Verify requirements.txt mirrors pyproject.toml dependency specs.

requirements.txt bakes the base dependencies plus selected extras for Docker
builds; its header demands every spec stay IDENTICAL to pyproject.toml. This
gate makes that promise checkable: every requirement listed in
requirements.txt that names a package present in pyproject's dependencies or
extras must carry the exact same specifier. Packages that appear only in
requirements.txt (image-only additions) are allowed but reported.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _name_of(spec: str) -> str:
    return (
        re.split(r"[<>=!~\[; ]", spec.strip(), maxsplit=1)[0].lower().replace("_", "-")
    )


def _pyproject_specs() -> dict[str, str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    specs: dict[str, str] = {}
    project = data["project"]
    for spec in project.get("dependencies", []):
        specs[_name_of(spec)] = spec.strip()
    for extra_specs in project.get("optional-dependencies", {}).values():
        for spec in extra_specs:
            specs.setdefault(_name_of(spec), spec.strip())
    return specs


def _requirements_specs() -> list[str]:
    out = []
    for line in (ROOT / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            out.append(line)
    return out


def main() -> int:
    pyproject = _pyproject_specs()
    mismatches: list[str] = []
    only_in_requirements: list[str] = []
    for spec in _requirements_specs():
        name = _name_of(spec)
        expected = pyproject.get(name)
        if expected is None:
            only_in_requirements.append(spec)
        elif spec.replace(" ", "") != expected.replace(" ", ""):
            mismatches.append(
                f"  {name}: requirements.txt='{spec}' pyproject='{expected}'"
            )

    if only_in_requirements:
        print("image-only requirements (not in pyproject — allowed):")
        for spec in only_in_requirements:
            print(f"  {spec}")
    if mismatches:
        print("MISMATCHED specs (requirements.txt must mirror pyproject.toml):")
        print("\n".join(mismatches))
        return 1
    print(
        f"requirements.txt in sync with pyproject.toml "
        f"({len(_requirements_specs())} specs checked)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
