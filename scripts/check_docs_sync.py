#!/usr/bin/env python3
"""Report `core/` changes whose documentation page was left untouched.

CLAUDE.md: "Code and docs ship together — no merge with stale docs." No linter,
pre-commit hook or CI job enforces that, so this maps each changed `core/`
module to the page that documents it and flags the ones nobody edited.

The mapping is intentionally blunt — it names the page a reviewer would expect
to move, not every page that could conceivably mention the module. Judgement
about *whether* the change is substantial stays with the caller.

    python scripts/check_docs_sync.py            # working tree
    python scripts/check_docs_sync.py main       # vs a base ref (CI: origin/<base>)

A change that deliberately ships without a doc update states so in a commit
message with the marker ``[docs-sync: skip]`` (plus the reason); the gate then
passes for that range and the marker stays in history for review.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = "mkdocs-site/docs"
CORE_MODULES_DIR = f"{DOCS_ROOT}/core-modules"

# Modules whose page name does not follow `core/<module>` -> `<module>.md`.
EXPLICIT_PAGES: dict[str, tuple[str, ...]] = {
    "api": (f"{DOCS_ROOT}/api/rest.md",),
    "routers": (f"{DOCS_ROOT}/api/rest.md",),
    "cli": (f"{DOCS_ROOT}/api/cli.md",),
    "observability": (
        f"{CORE_MODULES_DIR}/observability-module.md",
        f"{DOCS_ROOT}/advanced/observability.md",
    ),
    "tenancy": (f"{DOCS_ROOT}/advanced/multi-tenancy.md",),
    "goals": (f"{DOCS_ROOT}/plugins/goals.md",),
    "plugins": (
        f"{CORE_MODULES_DIR}/plugins.md",
        f"{DOCS_ROOT}/plugins/creating-plugins.md",
    ),
}

# Modules that are wiring or helpers: no page of their own, and a reviewer does
# not expect one to move.
UNDOCUMENTED_MODULES = frozenset(
    {"bootstrap", "interfaces", "static", "utils", "doc_sources"}
)

# Changing the agentic loop means the module map has to keep up.
AGENTIC_MODULES = frozenset(
    {"orchestration", "reasoning", "planning", "swarm", "meta", "world_model"}
)
AGENTIC_PAGE = f"{DOCS_ROOT}/architecture/agentic-patterns.md"


def _git(*args: str) -> list[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return [line for line in completed.stdout.splitlines() if line.strip()]


def changed_paths(base_ref: str | None) -> set[str]:
    if base_ref:
        return set(_git("diff", "--name-only", f"{base_ref}...HEAD"))
    paths = set(_git("diff", "--name-only"))
    paths |= set(_git("diff", "--cached", "--name-only"))
    paths |= {
        line[3:].strip() for line in _git("status", "--porcelain") if line[:2] == "??"
    }
    return paths


def expected_pages(module: str) -> tuple[str, ...]:
    if module in UNDOCUMENTED_MODULES:
        return ()
    if module in EXPLICIT_PAGES:
        return EXPLICIT_PAGES[module]
    candidate = f"{CORE_MODULES_DIR}/{module.replace('_', '-')}.md"
    if (REPO_ROOT / candidate).exists():
        return (candidate,)
    return ()


SKIP_MARKER = "[docs-sync: skip]"


def skip_requested(base_ref: str | None) -> bool:
    """True when a commit in the range carries the explicit opt-out marker."""
    log_range = f"{base_ref}..HEAD" if base_ref else "-1"
    return any(SKIP_MARKER in line for line in _git("log", "--format=%B", log_range))


def main() -> int:
    base_ref = sys.argv[1] if len(sys.argv) > 1 else None
    if skip_requested(base_ref):
        print(f"Docs sync: skipped — a commit in range carries {SKIP_MARKER}.")
        return 0
    paths = changed_paths(base_ref)

    touched_docs = {path for path in paths if path.startswith(f"{DOCS_ROOT}/")}
    changed_modules = sorted(
        {
            path.split("/")[1]
            for path in paths
            if path.startswith("core/") and path.endswith(".py") and "/" in path[5:]
        }
    )
    if not changed_modules:
        print("No core/ modules changed — docs sync not applicable.")
        return 0

    stale: list[str] = []
    unmapped: list[str] = []
    for module in changed_modules:
        pages = expected_pages(module)
        if not pages:
            if module not in UNDOCUMENTED_MODULES:
                unmapped.append(module)
            continue
        missing = [page for page in pages if page not in touched_docs]
        if missing:
            stale.append(f"  core/{module}/ -> {', '.join(missing)}")
        if module in AGENTIC_MODULES and AGENTIC_PAGE not in touched_docs:
            stale.append(f"  core/{module}/ -> {AGENTIC_PAGE}")

    print(f"Changed core modules: {', '.join(changed_modules)}")
    print(f"Touched docs: {', '.join(sorted(touched_docs)) or '(none)'}")

    if unmapped:
        print(
            "\nNo page mapped for: "
            + ", ".join(unmapped)
            + "\nDecide whether the module deserves one under "
            f"{CORE_MODULES_DIR}/, and add it to EXPLICIT_PAGES here either way."
        )

    if not stale:
        print("\nDocs sync: OK — every changed module's page was touched.")
        return 0

    print("\nDocs NOT updated for:")
    print("\n".join(sorted(set(stale))))
    print(
        "\nUpdate each page in this same change, or state explicitly why the "
        "change is not substantial enough to document."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
