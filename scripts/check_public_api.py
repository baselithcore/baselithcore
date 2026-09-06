"""Public API surface drift gate.

The versioning policy (mkdocs-site/docs/advanced/versioning-and-deprecation.md)
defines a breaking change as "removing or renaming a public symbol exported
from a ``core.*`` package ``__init__``". Nothing enforced that: a symbol could
vanish from an ``__all__`` in a ``fix:`` commit and ship as a patch release.

This gate snapshots every literal ``__all__`` under ``core/`` into
``scripts/public_api_baseline.json`` — the same shape as the OpenAPI drift
gate, for the Python surface — and fails on any difference:

- a **removed** symbol is reported as BREAKING: stage it through the
  deprecation process (announce, overlap for a MINOR, remove in a MAJOR) and
  refresh the baseline only in the change that carries the
  ``BREAKING CHANGE:`` footer;
- an **added** symbol just needs the baseline refreshed, so additions to the
  public surface are a conscious, reviewable line in the diff.

Only literal ``__all__`` lists are read (AST, no imports): a package that
builds its exports dynamically is skipped and reported by ``--list``.

Usage:
    python scripts/check_public_api.py                    # gate
    python scripts/check_public_api.py --list             # current surface
    python scripts/check_public_api.py --update-baseline  # record the change
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).resolve().parent / "public_api_baseline.json"
SCANNED_ROOT = "core"
EXCLUDED_DIR_NAMES = frozenset({"__pycache__", "build", "dist", "node_modules"})
POLICY_DOC = "mkdocs-site/docs/advanced/versioning-and-deprecation.md"


def read_literal_all(init_py: Path) -> list[str] | None:
    """The sorted ``__all__`` of a module when it is a literal list/tuple of strings.

    Returns ``None`` when the module has no ``__all__`` or builds it
    dynamically (concatenation, ``+=``, comprehension), which the gate cannot
    snapshot without importing.
    """
    try:
        tree = ast.parse(init_py.read_text(encoding="utf-8"), filename=str(init_py))
    except SyntaxError:
        return None
    names: list[str] | None = None
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        if isinstance(node, ast.AugAssign):
            return None
        if isinstance(value, ast.List | ast.Tuple) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
        ):
            names = sorted({e.value for e in value.elts})  # type: ignore[union-attr]
        else:
            return None
    return names


def _module_name(root: Path, init_py: Path) -> str:
    return ".".join(init_py.relative_to(root).parent.parts)


def collect_public_api(root: Path) -> dict[str, list[str]]:
    """``{dotted package: sorted __all__}`` for every literal ``__all__`` under core/."""
    surface: dict[str, list[str]] = {}
    for init_py in sorted((root / SCANNED_ROOT).rglob("__init__.py")):
        relative = init_py.relative_to(root)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
            continue
        names = read_literal_all(init_py)
        if names is not None:
            surface[_module_name(root, init_py)] = names
    return surface


def dynamic_packages(root: Path) -> list[str]:
    """Packages whose ``__all__`` is absent or dynamic (not covered by the gate)."""
    covered = set(collect_public_api(root))
    return sorted(
        _module_name(root, init_py)
        for init_py in (root / SCANNED_ROOT).rglob("__init__.py")
        if "__pycache__" not in init_py.parts
        and _module_name(root, init_py) not in covered
    )


@dataclass
class ApiDiff:
    """Symbols that differ between the baseline and the current surface."""

    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.added and not self.removed


def diff_public_api(
    baseline: dict[str, list[str]], current: dict[str, list[str]]
) -> ApiDiff:
    """Compute per-package added/removed symbols (a vanished package removes all)."""
    result = ApiDiff()
    for package in sorted(set(baseline) | set(current)):
        before = set(baseline.get(package, []))
        after = set(current.get(package, []))
        if after - before:
            result.added[package] = sorted(after - before)
        if before - after:
            result.removed[package] = sorted(before - after)
    return result


def load_baseline(baseline_path: Path) -> dict[str, list[str]]:
    """Load the frozen surface, tolerating a missing baseline."""
    if not baseline_path.exists():
        return {}
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    return {
        str(k): sorted(str(n) for n in v)
        for k, v in payload.get("packages", {}).items()
    }


def check_public_api(root: Path, *, baseline: dict[str, list[str]]) -> list[str]:
    """Return human-readable violations for any drift from the baseline."""
    diff = diff_public_api(baseline, collect_public_api(root))
    violations: list[str] = []
    for package, names in diff.removed.items():
        violations.append(
            f"BREAKING: {package} no longer exports {', '.join(names)} — stage it through "
            f"the deprecation process ({POLICY_DOC}); refresh the baseline only in the "
            "change carrying the 'BREAKING CHANGE:' footer"
        )
    for package, names in diff.added.items():
        violations.append(
            f"{package} newly exports {', '.join(names)} — run "
            "'python scripts/check_public_api.py --update-baseline' to record it"
        )
    return violations


def write_baseline(root: Path, baseline_path: Path) -> int:
    """Rewrite the baseline from the current tree; return the symbol count."""
    surface = collect_public_api(root)
    payload = {
        "_comment": (
            "Literal __all__ of every core package. Removing a symbol is a breaking change "
            "under the versioning policy; refresh with: "
            "python scripts/check_public_api.py --update-baseline"
        ),
        "packages": surface,
    }
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sum(len(v) for v in surface.values())


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline", action="store_true", help="record the current surface"
    )
    parser.add_argument(
        "--list", action="store_true", help="print the surface and uncovered packages"
    )
    args = parser.parse_args(argv)

    if args.list:
        for package, names in collect_public_api(REPO_ROOT).items():
            print(f"{package}: {len(names)} symbol(s)")
        uncovered = dynamic_packages(REPO_ROOT)
        if uncovered:
            print("\nNot covered (no literal __all__):", ", ".join(uncovered))
        return 0

    if args.update_baseline:
        total = write_baseline(REPO_ROOT, BASELINE_PATH)
        print(f"Baseline refreshed: {total} public symbol(s) recorded.")
        return 0

    violations = check_public_api(REPO_ROOT, baseline=load_baseline(BASELINE_PATH))
    if violations:
        print("Public API surface drift:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1
    print("Public API surface OK (matches scripts/public_api_baseline.json).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
