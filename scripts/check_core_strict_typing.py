"""Strict-typing ratchet for the core kernel.

The repo-wide mypy run (``mypy baselith/core/``) is deliberately lenient:
``disallow_untyped_defs`` is off and ``warn_return_any`` is off, because a
big-bang switch to strict mode across 140k lines would never land. This gate
is the ratchet that gets there package by package:

- every package in :data:`STRICT_CORE_PACKAGES` is checked with
  :data:`STRICT_FLAGS` on every commit (pre-commit) and in CI;
- a package enters the allowlist once it passes, and never leaves — an entry
  that starts failing is a regression in that package, not a reason to drop
  the entry;
- ``--candidates`` lists the packages not yet allowlisted that already pass,
  so growing the list is a one-line change, not an investigation.

It supersedes the file-level ``check_core_resilience_typing.py`` gate: the
four resilience modules it covered sit inside ``baselith.core.resilience``, which is
allowlisted here as a whole package.

Usage:
    python scripts/check_core_strict_typing.py              # gate
    python scripts/check_core_strict_typing.py --list       # allowlist
    python scripts/check_core_strict_typing.py --candidates # ready to add
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mypy_runner import MypyNotFoundError, mypy_base_command

REPO_ROOT = Path(__file__).resolve().parents[1]

#: mypy flags every allowlisted package must satisfy. ``--follow-imports=silent``
#: type-checks what an allowlisted package imports (so a call into another
#: core package carries its real return type instead of ``Any``) but reports
#: errors only for the allowlisted files themselves. The CI job installs mypy
#: plus a handful of stubs, not the project: third-party modules that are
#: missing resolve to ``Any`` under ``--ignore-missing-imports``.
#:
#: ``--warn-unused-ignores`` is deliberately absent: whether a ``type: ignore``
#: on a third-party call is "unused" depends on whether that library is
#: installed, so the flag would make the gate disagree between a developer's
#: full environment and the stub-only CI job.
STRICT_FLAGS: tuple[str, ...] = (
    "--ignore-missing-imports",
    "--follow-imports=silent",
    "--no-error-summary",
    "--disallow-untyped-defs",
    "--disallow-incomplete-defs",
    "--warn-return-any",
    "--no-implicit-optional",
    "--check-untyped-defs",
)

#: Packages (or top-level ``core.<module>`` files) that pass
#: :data:`STRICT_FLAGS`. Sorted, dotted names. Grow only.
STRICT_CORE_PACKAGES: tuple[str, ...] = (
    # The public facade. Not under ``baselith/core/`` — and so never a ``--candidates``
    # suggestion — but it is the one package every downstream program imports,
    # so it is strict from its first commit.
    "baselith",
    "baselith.core._core_version",
    "baselith.core._version",
    "baselith.core.a2a",
    "baselith.core.adversarial",
    "baselith.core.agent",
    "baselith.core.api",
    "baselith.core.auth",
    "baselith.core.bootstrap",
    "baselith.core.cache",
    "baselith.core.chat",
    "baselith.core.cli",
    "baselith.core.compliance",
    "baselith.core.config",
    "baselith.core.connectors",
    "baselith.core.context",
    "baselith.core.db",
    "baselith.core.di",
    "baselith.core.doc_sources",
    "baselith.core.evaluation",
    "baselith.core.events",
    "baselith.core.exceptions",
    "baselith.core.exploration",
    "baselith.core.feature_flags",
    "baselith.core.finetuning",
    "baselith.core.graph",
    "baselith.core.guardrails",
    "baselith.core.human",
    "baselith.core.incidents",
    "baselith.core.interfaces",
    "baselith.core.learning",
    "baselith.core.lifecycle",
    "baselith.core.loops",
    "baselith.core.marketplace",
    "baselith.core.mcp",
    "baselith.core.memory",
    "baselith.core.meta",
    "baselith.core.middleware",
    "baselith.core.models",
    "baselith.core.nlp",
    "baselith.core.observability",
    "baselith.core.optimization",
    "baselith.core.orchestration",
    "baselith.core.personas",
    "baselith.core.planning",
    "baselith.core.plugins",
    "baselith.core.prioritization",
    "baselith.core.privacy",
    "baselith.core.prompts",
    "baselith.core.quotas",
    "baselith.core.realtime",
    "baselith.core.reasoning",
    "baselith.core.reflection",
    "baselith.core.registries",
    "baselith.core.resilience",
    "baselith.core.routers",
    "baselith.core.scraper",
    "baselith.core.security",
    "baselith.core.services.chat",
    "baselith.core.services.graph",
    "baselith.core.services.indexing",
    "baselith.core.services.llm",
    "baselith.core.services.retrieval",
    "baselith.core.services.sandbox",
    "baselith.core.services.tenant",
    "baselith.core.services.vectorstore",
    "baselith.core.services.vision",
    "baselith.core.services.voice",
    "baselith.core.skill_evolution",
    "baselith.core.storage",
    "baselith.core.swarm",
    "baselith.core.task_queue",
    "baselith.core.tenancy",
    "baselith.core.thirdparty",
    "baselith.core.transparency",
    "baselith.core.utils",
    "baselith.core.webhooks",
    "baselith.core.workflows",
    "baselith.core.world_model",
)


def package_path(root: Path, name: str) -> Path:
    """Filesystem target of a dotted name under ``root``: a package directory
    or, for a top-level module such as ``baselith.core.context``, its ``.py`` file."""
    directory = root.joinpath(*name.split("."))
    if name == "baselith":
        # The facade only: the package also holds ``core`` and ``plugins``.
        return directory / "__init__.py"
    if directory.is_dir():
        return directory
    return directory.with_suffix(".py")


def _subpackages(directory: Path, prefix: str) -> list[str]:
    return [
        f"{prefix}.{child.name}"
        for child in directory.iterdir()
        if child.is_dir() and (child / "__init__.py").is_file()
    ]


def all_core_packages(
    root: Path, *, allowlist: Iterable[str] = STRICT_CORE_PACKAGES
) -> list[str]:
    """Every ``core.*`` package or module the gate could check, sorted.

    Top-level by default. A package that is only *partially* covered — one of
    its subpackages is allowlisted, as ``baselith.core.services.llm`` is — is replaced
    by its own subpackages, so ``--candidates`` names the unit that can
    actually be added instead of a parent that will never go green as a whole.
    """
    core = root / "baselith" / "core"
    listed = set(allowlist)
    names: list[str] = []
    for child in core.iterdir():
        if child.is_dir() and (child / "__init__.py").is_file():
            dotted = f"baselith.core.{child.name}"
            partially_covered = any(e.startswith(f"{dotted}.") for e in listed)
            names.extend(_subpackages(child, dotted) if partially_covered else [dotted])
        elif child.suffix == ".py" and child.name != "__init__.py":
            names.append(f"baselith.core.{child.stem}")
    return sorted(names)


def candidate_packages(
    root: Path, *, allowlist: Iterable[str] = STRICT_CORE_PACKAGES
) -> list[str]:
    """Top-level core packages not yet in the allowlist."""
    listed = set(allowlist)
    return [
        name
        for name in all_core_packages(root, allowlist=allowlist)
        if name not in listed
    ]


def run_mypy(
    targets: Sequence[str], *, cwd: Path = REPO_ROOT, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run mypy with :data:`STRICT_FLAGS` on ``targets`` (relative paths)."""
    cmd = [*mypy_base_command(), *STRICT_FLAGS, *targets]
    return subprocess.run(cmd, cwd=cwd, capture_output=capture, text=True)


def _relative_targets(root: Path, names: Iterable[str]) -> list[str]:
    return [package_path(root, name).relative_to(root).as_posix() for name in names]


def _print_candidates(root: Path) -> int:
    ready: list[str] = []
    for name in candidate_packages(root):
        completed = run_mypy(_relative_targets(root, (name,)), cwd=root, capture=True)
        errors = completed.stdout.count(": error:")
        status = "ready" if errors == 0 else f"{errors} error(s)"
        print(f"{name}: {status}")
        if errors == 0:
            ready.append(name)
    if ready:
        print("\nAdd to STRICT_CORE_PACKAGES:", ", ".join(ready))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="print the allowlisted packages"
    )
    parser.add_argument(
        "--candidates",
        action="store_true",
        help="run mypy on the packages not yet allowlisted and report the ones that pass",
    )
    args = parser.parse_args(argv)

    if args.list:
        print("\n".join(STRICT_CORE_PACKAGES))
        return 0

    try:
        mypy_base_command()
    except MypyNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.candidates:
        return _print_candidates(REPO_ROOT)

    completed = run_mypy(_relative_targets(REPO_ROOT, STRICT_CORE_PACKAGES))
    if completed.returncode == 0:
        print(f"Core strict typing OK ({len(STRICT_CORE_PACKAGES)} packages).")
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
