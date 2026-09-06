"""Silent-exception ratchet for ``core/``.

A broad handler that swallows the error without a trace —

    except Exception:
        pass

— is the failure mode that turns a production incident into "it just
returned nothing". This gate freezes the current count of such handlers per
file and lets it only shrink:

- a handler is *silent* when it catches ``Exception``, ``BaseException`` or
  is a bare ``except:``, and its body contains no call (no log line, no
  metric), no ``raise``, no ``await`` and no ``yield`` — nothing that could
  make the failure observable;
- a silent handler that is a deliberate, documented degradation opts out
  with a marker on the ``except`` line::

      except Exception:  # silent-ok: best-effort cache warmup, never blocks boot

  The reason is mandatory and becomes the audit trail;
- ``scripts/exception_hygiene_baseline.json`` freezes today's per-file counts
  (same ratchet as ``check_file_size.py``): a new silent handler fails the
  build, a baselined file may shrink but never grow, and an entry must be
  deleted once its file reaches zero.

Usage:
    python scripts/check_exception_hygiene.py                    # gate
    python scripts/check_exception_hygiene.py --list             # every site
    python scripts/check_exception_hygiene.py --update-baseline  # after fixes
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = Path(__file__).resolve().parent / "exception_hygiene_baseline.json"

#: Trees scanned by the gate, relative to the repository root.
SCANNED_ROOTS: tuple[str, ...] = ("core",)
#: Marker that opts a handler out of the gate; a reason must follow it.
OPT_OUT_MARKER = "silent-ok:"
BROAD_EXCEPTION_NAMES = frozenset({"Exception", "BaseException"})
EXCLUDED_DIR_NAMES = frozenset({"__pycache__", "build", "dist", "node_modules"})
_OBSERVABLE_NODES = (ast.Call, ast.Raise, ast.Await, ast.Yield, ast.YieldFrom)


@dataclass(frozen=True)
class SilentHandler:
    """One broad ``except`` whose body makes the failure unobservable."""

    path: str
    line: int
    kind: str


def _broad_kind(handler: ast.ExceptHandler) -> str | None:
    """``"Exception"``/``"BaseException"``/``"bare"`` for a broad handler, else None."""
    if handler.type is None:
        return "bare"
    elements = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    for element in elements:
        if isinstance(element, ast.Name) and element.id in BROAD_EXCEPTION_NAMES:
            return element.id
        if isinstance(element, ast.Attribute) and element.attr in BROAD_EXCEPTION_NAMES:
            return element.attr
    return None


def _is_silent(handler: ast.ExceptHandler) -> bool:
    return not any(
        isinstance(node, _OBSERVABLE_NODES)
        for statement in handler.body
        for node in ast.walk(statement)
    )


def find_silent_handlers(
    path: Path, *, relative_to: Path | None = None
) -> list[SilentHandler]:
    """Return the silent broad handlers in one Python source file, in line order."""
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    lines = source.splitlines()
    shown = path.relative_to(relative_to).as_posix() if relative_to else path.as_posix()
    found: list[SilentHandler] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        kind = _broad_kind(node)
        if kind is None or not _is_silent(node):
            continue
        if OPT_OUT_MARKER in lines[node.lineno - 1]:
            continue
        found.append(SilentHandler(shown, node.lineno, kind))
    return sorted(found, key=lambda h: h.line)


def iter_source_files(root: Path) -> Iterable[Path]:
    """Every ``.py`` file under the scanned roots, excluding build/cache dirs."""
    for scanned in SCANNED_ROOTS:
        base = root / scanned
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relative = path.relative_to(root)
            if any(part in EXCLUDED_DIR_NAMES for part in relative.parts):
                continue
            yield path


def scan_tree(root: Path) -> dict[str, int]:
    """``{relative path: silent handler count}`` for files with at least one."""
    counts: dict[str, int] = {}
    for path in iter_source_files(root):
        found = find_silent_handlers(path, relative_to=root)
        if found:
            counts[found[0].path] = len(found)
    return counts


def load_baseline(baseline_path: Path) -> dict[str, int]:
    """Load the frozen per-file counts, tolerating a missing baseline."""
    if not baseline_path.exists():
        return {}
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    return {str(key): int(value) for key, value in payload.get("files", {}).items()}


def check_exception_hygiene(root: Path, *, baseline: dict[str, int]) -> list[str]:
    """Compare the tree against the baseline and return human-readable violations."""
    current = scan_tree(root)
    violations: list[str] = []
    for relative_path, count in sorted(current.items()):
        frozen = baseline.get(relative_path)
        if frozen is None:
            violations.append(
                f"{relative_path}: {count} silent broad except handler(s); log, "
                f"narrow, re-raise, or mark it '# {OPT_OUT_MARKER} <reason>'"
            )
        elif count > frozen:
            violations.append(
                f"{relative_path}: grew to {count} silent handler(s) (frozen at {frozen}); "
                "silent handlers may only shrink"
            )
    for relative_path in sorted(baseline):
        if relative_path not in current:
            violations.append(
                f"{relative_path}: no silent handlers left (or file gone); "
                "remove it from scripts/exception_hygiene_baseline.json"
            )
    return violations


def write_baseline(root: Path, baseline_path: Path) -> int:
    """Rewrite the baseline from the current tree; return the total site count."""
    counts = scan_tree(root)
    payload = {
        "_comment": (
            "Silent broad except handlers per core/ file when the gate was introduced. "
            "Counts may shrink but never grow; delete an entry once the file reaches zero. "
            "Regenerate with: python scripts/check_exception_hygiene.py --update-baseline"
        ),
        "files": dict(sorted(counts.items())),
    }
    baseline_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return sum(counts.values())


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-baseline", action="store_true", help="freeze current counts"
    )
    parser.add_argument(
        "--list", action="store_true", help="print every silent handler"
    )
    args = parser.parse_args(argv)

    if args.list:
        for path in iter_source_files(REPO_ROOT):
            for handler in find_silent_handlers(path, relative_to=REPO_ROOT):
                print(f"{handler.path}:{handler.line}: {handler.kind}")
        return 0

    if args.update_baseline:
        total = write_baseline(REPO_ROOT, BASELINE_PATH)
        print(f"Baseline refreshed: {total} silent handler(s) frozen.")
        return 0

    violations = check_exception_hygiene(
        REPO_ROOT, baseline=load_baseline(BASELINE_PATH)
    )
    if violations:
        print("Exception hygiene violations:", file=sys.stderr)
        for violation in violations:
            print(f" - {violation}", file=sys.stderr)
        return 1
    print("Exception hygiene OK (silent handlers at or below baseline).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
