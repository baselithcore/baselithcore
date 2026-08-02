"""Enforce the 500-line file size cap declared in CLAUDE.md.

The checker is a *ratchet*, not a big-bang cleanup:

- any first-party source file must stay at or below :data:`MAX_LINES`;
- files that already exceeded the cap when the gate was introduced are frozen in
  ``scripts/file_size_baseline.json`` at their then-current length — they may
  shrink freely, but never grow;
- once a baselined file drops back to the cap it must be removed from the
  baseline, so the debt list can only ever get shorter.

Run ``python scripts/check_file_size.py --update-baseline`` after legitimately
splitting a file to refresh the frozen counts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).resolve().parent / "file_size_baseline.json"

MAX_LINES = 500

CHECKED_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".vue"})

# Directory names pruned anywhere in the tree: dependency trees, build output,
# coverage reports, caches and vendored third-party code are not first-party
# source, so the cap does not apply to them.
EXCLUDED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        "site-packages",
        "vendor",
        "venv",
    }
)

# Top-level trees excluded from the cap: `templates/` ships project scaffolding
# rendered into *other* repos, and `backstage-portal/` is vendored Backstage
# scaffolding. Both are already excluded from ruff and mypy.
EXCLUDED_PATH_PREFIXES = ("templates/", "backstage-portal/")

# Bundler output committed for wheel packaging. The filenames are content-hashed
# (`index-DFgg2bSS.js`), so baselining them would rot on every rebuild.
EXCLUDED_PATH_FRAGMENTS = ("static/assets/",)

# Minified bundles are a single logical line-per-chunk; the cap is meaningless.
EXCLUDED_NAME_SUFFIXES = (".min.js", ".min.ts", ".bundle.js")


def iter_source_files(root: Path) -> Iterable[Path]:
    """Yield every first-party source file subject to the line cap."""
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in CHECKED_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
            continue
        posix_path = relative.as_posix()
        if posix_path.startswith(EXCLUDED_PATH_PREFIXES):
            continue
        if any(fragment in posix_path for fragment in EXCLUDED_PATH_FRAGMENTS):
            continue
        if path.name.endswith(EXCLUDED_NAME_SUFFIXES):
            continue
        yield path


def count_lines(path: Path) -> int:
    """Return the number of physical lines in a source file.

    Counts newline-terminated lines plus a trailing unterminated one, matching
    ``wc -l`` semantics. ``str.splitlines`` is deliberately avoided: it also
    breaks on form feeds and U+2028/U+2029, which would make the reported count
    disagree with every other tool a developer might reach for.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def load_baseline(baseline_path: Path) -> dict[str, int]:
    """Load the frozen over-cap file sizes, tolerating a missing baseline."""
    if not baseline_path.exists():
        return {}
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in payload.get("files", {}).items()}


def measure(root: Path) -> dict[str, int]:
    """Return ``{relative path: line count}`` for every checked source file."""
    return {
        path.relative_to(root).as_posix(): count_lines(path)
        for path in iter_source_files(root)
    }


def check_file_sizes(
    root: Path,
    *,
    baseline: dict[str, int] | None = None,
    max_lines: int = MAX_LINES,
) -> list[str]:
    """Validate the line cap against the ratchet baseline and return violations."""
    frozen = dict(baseline or {})
    violations: list[str] = []
    sizes = measure(root)

    for relative_path, line_count in sorted(sizes.items()):
        allowed = frozen.get(relative_path)

        if line_count <= max_lines:
            if allowed is not None:
                violations.append(
                    f"{relative_path}: now {line_count} lines (<= {max_lines}); "
                    "remove it from scripts/file_size_baseline.json"
                )
            continue

        if allowed is None:
            violations.append(
                f"{relative_path}: {line_count} lines exceeds the {max_lines}-line cap; "
                "split the module instead of baselining it"
            )
        elif line_count > allowed:
            violations.append(
                f"{relative_path}: grew to {line_count} lines (frozen at {allowed}); "
                "over-cap files may only shrink"
            )

    for relative_path in sorted(frozen):
        if relative_path not in sizes:
            violations.append(
                f"{relative_path}: baselined file no longer exists; "
                "remove it from scripts/file_size_baseline.json"
            )

    return violations


def write_baseline(
    root: Path, baseline_path: Path, *, max_lines: int = MAX_LINES
) -> int:
    """Rewrite the baseline from the current tree and return the frozen file count."""
    over_cap = {
        relative_path: line_count
        for relative_path, line_count in sorted(measure(root).items())
        if line_count > max_lines
    }
    payload = {
        "_comment": (
            f"Files that exceeded the {max_lines}-line cap when the gate was introduced. "
            "They may shrink but never grow; delete an entry once the file is under the cap. "
            "Regenerate with: python scripts/check_file_size.py --update-baseline"
        ),
        "max_lines": max_lines,
        "files": over_cap,
    }
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return len(over_cap)


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="freeze the current over-cap files instead of checking them",
    )
    args = parser.parse_args()

    if args.update_baseline:
        frozen_count = write_baseline(REPO_ROOT, BASELINE_PATH)
        print(
            f"Baseline refreshed: {frozen_count} file(s) over the {MAX_LINES}-line cap."
        )
        return 0

    violations = check_file_sizes(REPO_ROOT, baseline=load_baseline(BASELINE_PATH))
    if violations:
        print(f"File size cap violations ({MAX_LINES} lines):", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1

    print(f"File sizes OK (cap {MAX_LINES} lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
