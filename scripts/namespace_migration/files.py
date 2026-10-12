"""Which files to read, and which of them a local ``core``/``plugins`` shadows."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from .config import OLD_TOPS, is_excluded, is_text


def list_files(root: Path) -> list[str]:
    """Tracked plus untracked-but-not-ignored files, repo-relative and sorted.

    Untracked files count: the codemod is re-run on in-flight branches whose
    newest modules may not be committed yet. Outside a git checkout every
    regular file under *root* is listed instead.
    """
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        )
        names = {
            n for n in proc.stdout.decode("utf-8", "surrogateescape").split("\0") if n
        }
    except (OSError, subprocess.CalledProcessError):
        names = {
            p.relative_to(root).as_posix()
            for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts
        }
    return sorted(n for n in names if (root / n).is_file())


def candidate_files(files: Iterable[str]) -> list[str]:
    """Files in scope: text, not excluded."""
    return [f for f in files if is_text(f) and not is_excluded(f)]


def shadow_roots(files: Iterable[str]) -> dict[str, frozenset[str]]:
    """Directories that put their own ``core``/``plugins`` package first.

    A directory ``D`` shadows a legacy top for the files beneath it when
    ``D/<top>/__init__.py`` exists and ``D`` is not itself a package — that
    is, ``D`` is plausibly an import root of a vendored tree, whose absolute
    ``import core`` means *its* package, not the framework's.
    """
    present = set(files)
    found: dict[str, set[str]] = {}
    for rel in present:
        parts = PurePosixPath(rel).parts
        if len(parts) < 3 or parts[-1] != "__init__.py" or parts[-2] not in OLD_TOPS:
            continue
        parent = "/".join(parts[:-2])
        if f"{parent}/__init__.py" in present:
            continue
        found.setdefault(parent, set()).add(parts[-2])
    return {k: frozenset(v) for k, v in sorted(found.items())}


def shadowed_for(rel: str, roots: dict[str, frozenset[str]]) -> frozenset[str]:
    """Legacy tops shadowed for repo-relative file *rel*."""
    hit: set[str] = set()
    for parent, tops in roots.items():
        if rel.startswith(parent + "/"):
            hit |= tops
    return frozenset(hit)


__all__ = ["candidate_files", "list_files", "shadow_roots", "shadowed_for"]
