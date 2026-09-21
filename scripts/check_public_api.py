"""Public API surface drift gate.

The versioning policy (mkdocs-site/docs/advanced/versioning-and-deprecation.md)
defines a breaking change as "removing or renaming a public symbol exported
from a ``core.*`` package ``__init__``". Nothing enforced that: a symbol could
vanish from an ``__all__`` in a ``fix:`` commit and ship as a patch release.

This gate snapshots every literal ``__all__`` under ``baselith/`` (the curated
public facade) and ``core/`` (the implementation) into
``scripts/public_api_baseline.json`` — the same shape as the OpenAPI drift
gate, for the Python surface — and fails on any difference:

- a **removed** symbol is reported with what its removal costs, which depends
  on the tier its package declares in :data:`core.stability.PACKAGE_STABILITY`
  — MAJOR and a ``BREAKING CHANGE:`` footer for a ``stable`` package, a MINOR
  after a deprecation cycle for ``beta``, a plain MINOR for ``experimental``.
  Without tiers every one of the 1329 symbols carried the strictest of those,
  and retiring an experiment cost a MAJOR release;
- an **added** symbol just needs the baseline refreshed, so additions to the
  public surface are a conscious, reviewable line in the diff.

The same pass keeps the tier table honest:

- it must classify **exactly** the packages that exist — a new package cannot
  arrive unclassified, and an entry cannot outlive its package;
- a tier may move toward stability, or to ``deprecated``, and never back down
  the ladder. Weakening a promise already published is itself breaking.

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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ``core.stability`` is a data table on stdlib only — importing it costs
# nothing and is far less brittle than parsing the dict back out of the AST.
# It is the one import this gate makes; the surface itself is still read
# without importing a single module of the tree.
from core.stability import (  # noqa: E402
    PACKAGE_STABILITY,
    Stability,
    stability_of,
)

BASELINE_PATH = Path(__file__).resolve().parent / "public_api_baseline.json"
#: Packages whose literal ``__all__`` is the public contract. ``baselith`` is
#: the curated facade (the ``stable`` tier); ``core`` is the implementation,
#: whose packages each declare their own tier.
SCANNED_ROOTS = ("baselith", "core")
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
    """``{dotted package: sorted __all__}`` for every literal ``__all__`` scanned."""
    surface: dict[str, list[str]] = {}
    for scanned_root in SCANNED_ROOTS:
        for init_py in sorted((root / scanned_root).rglob("__init__.py")):
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
        for scanned_root in SCANNED_ROOTS
        for init_py in (root / scanned_root).rglob("__init__.py")
        if "__pycache__" not in init_py.parts
        and _module_name(root, init_py) not in covered
    )


#: Tiers a package may move *to* from a given tier. Promotion along the
#: ladder is always allowed and retirement is always allowed; sliding back
#: down it is not, because the weaker promise was already published.
_ALLOWED_TIER_MOVES: dict[Stability, frozenset[Stability]] = {
    Stability.EXPERIMENTAL: frozenset(
        {Stability.EXPERIMENTAL, Stability.BETA, Stability.STABLE, Stability.DEPRECATED}
    ),
    Stability.BETA: frozenset({Stability.BETA, Stability.STABLE, Stability.DEPRECATED}),
    Stability.STABLE: frozenset({Stability.STABLE, Stability.DEPRECATED}),
    Stability.DEPRECATED: frozenset({Stability.DEPRECATED}),
}

#: What removing a symbol from a package of each tier costs, in the words the
#: release process uses. Read by the violation messages so the semver decision
#: is computed from the declared promise instead of recalled by the author.
_REMOVAL_COST: dict[Stability, str] = {
    Stability.STABLE: (
        "BREAKING (MAJOR) — {package} is stable: keep the old name working for at "
        "least one MINOR, then remove it in the change carrying the "
        "'BREAKING CHANGE:' footer"
    ),
    Stability.BETA: (
        "MINOR after a deprecation cycle — {package} is beta: the removal is only "
        "in order if the DeprecationWarning shipped at least one MINOR ago"
    ),
    Stability.EXPERIMENTAL: (
        "MINOR — {package} is experimental and promises no compatibility"
    ),
    Stability.DEPRECATED: (
        "MAJOR — {package} is a retired compatibility shim; removing it is the "
        "announced end of its overlap window"
    ),
}


def top_level_packages(root: Path) -> set[str]:
    """Every top-level package the stability table has to classify."""
    found: set[str] = set()
    for scanned_root in SCANNED_ROOTS:
        root_init = root / scanned_root / "__init__.py"
        if root_init.is_file():
            found.add(scanned_root)
        for child in (root / scanned_root).glob("*/__init__.py"):
            if child.parent.name not in EXCLUDED_DIR_NAMES:
                found.add(f"{scanned_root}.{child.parent.name}")
    # ``core`` itself is the namespace, not a classified surface: its
    # ``__init__`` exports only ``__version__``.
    return {name for name in found if name != "core"}


def check_stability(root: Path, *, baseline_tiers: dict[str, str]) -> list[str]:
    """Violations for the tier table: completeness, then the ratchet.

    Kept apart from :func:`check_public_api` because the two answer different
    questions — "did the surface move?" and "is every promise still declared
    and still as strong as published?" — and because only this one reads the
    real tree.
    """
    return check_stability_table(root) + check_tier_ratchet(baseline_tiers)


def check_stability_table(root: Path) -> list[str]:
    """Violations for a stability table that no longer describes the tree."""
    declared = set(PACKAGE_STABILITY)
    actual = top_level_packages(root)
    violations = [
        f"{package} has no stability tier — classify it in core/stability.py "
        "(stable / beta / experimental / deprecated)"
        for package in sorted(actual - declared)
    ]
    violations.extend(
        f"core/stability.py classifies {package}, which no longer exists — "
        "drop the entry"
        for package in sorted(declared - actual)
    )
    return violations


def check_tier_ratchet(baseline_tiers: dict[str, str]) -> list[str]:
    """Violations for a package whose promise got weaker."""
    violations = []
    for package, recorded in sorted(baseline_tiers.items()):
        current = PACKAGE_STABILITY.get(package)
        if current is None:
            continue
        try:
            previous = Stability(recorded)
        except ValueError:
            continue
        if current not in _ALLOWED_TIER_MOVES[previous]:
            violations.append(
                f"{package} was published as '{previous}' and is now '{current}' — "
                "a tier moves toward stability or to 'deprecated', never back: "
                "weakening a promise already made is itself a breaking change"
            )
    return violations


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


def load_baseline_tiers(baseline_path: Path) -> dict[str, str]:
    """The tiers recorded the last time the baseline was refreshed."""
    if not baseline_path.exists():
        return {}
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in payload.get("stability", {}).items()}


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
        cost = _REMOVAL_COST[stability_of(package)].format(package=package)
        violations.append(
            f"{package} no longer exports {', '.join(names)} — {cost}. "
            f"See {POLICY_DOC}; refresh the baseline in that same change"
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
            "Literal __all__ of every baselith and core package. Removing a symbol is a "
            "breaking change under the versioning policy; refresh with: "
            "python scripts/check_public_api.py --update-baseline"
        ),
        "packages": surface,
        "stability": {
            package: str(tier) for package, tier in sorted(PACKAGE_STABILITY.items())
        },
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
            print(f"{package}: {len(names)} symbol(s) [{stability_of(package)}]")
        uncovered = dynamic_packages(REPO_ROOT)
        if uncovered:
            print("\nNot covered (no literal __all__):", ", ".join(uncovered))
        return 0

    if args.update_baseline:
        total = write_baseline(REPO_ROOT, BASELINE_PATH)
        print(f"Baseline refreshed: {total} public symbol(s) recorded.")
        return 0

    violations = check_public_api(REPO_ROOT, baseline=load_baseline(BASELINE_PATH))
    violations += check_stability(
        REPO_ROOT, baseline_tiers=load_baseline_tiers(BASELINE_PATH)
    )
    if violations:
        print("Public API surface drift:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1
    census = Counter(PACKAGE_STABILITY.values())
    tiers = ", ".join(f"{census[tier]} {tier}" for tier in Stability if census[tier])
    print(
        "Public API surface OK (matches scripts/public_api_baseline.json). "
        f"Tiers: {tiers}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
