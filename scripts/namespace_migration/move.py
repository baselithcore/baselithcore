"""The filesystem half: ``core/`` → ``baselith/core/``, ``plugins/`` → ``baselith/plugins/``."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import NEW_ROOT, OLD_TOPS


def pending_moves(root: Path) -> list[str]:
    """Legacy top-level directories still present at *root*, as ``old -> new``."""
    return [
        f"{top}/ -> {NEW_ROOT}/{top}/"
        for top in OLD_TOPS
        if (root / top).is_dir() and any((root / top).rglob("*.py"))
    ]


def _is_git(root: Path) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def _tracked(root: Path, top: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--", top],
        capture_output=True,
        text=True,
    )
    return bool(proc.stdout.strip())


def _merge_into(src: Path, dst: Path) -> None:
    """Move whatever is left under *src* into *dst*, then drop *src*."""
    for entry in sorted(src.iterdir()):
        target = dst / entry.name
        if entry.name == "__pycache__":
            shutil.rmtree(entry, ignore_errors=True)
        elif entry.is_dir() and target.is_dir():
            _merge_into(entry, target)
        elif not target.exists():
            shutil.move(str(entry), str(target))
    shutil.rmtree(src, ignore_errors=True)


def move_tree(root: Path) -> list[str]:
    """Perform the moves; return what was moved.

    ``git mv`` when the directory is tracked, so history follows the rename.
    Untracked or ignored leftovers (plugin ``.env`` files, local build output)
    are merged into the new location afterwards rather than left behind,
    because a stray ``core/`` at the root would shadow nothing but confuse
    every tool that globs for it.
    """
    moved: list[str] = []
    new_root = root / NEW_ROOT
    new_root.mkdir(exist_ok=True)
    git = _is_git(root)
    for top in OLD_TOPS:
        src = root / top
        if not src.is_dir():
            continue
        dst = new_root / top
        if dst.exists() and any(dst.rglob("*.py")):
            raise RuntimeError(f"both {src} and {dst} exist; refusing to merge trees")
        if dst.exists():
            # Ignored leftovers of an earlier layout (``__pycache__``, build
            # output). ``git mv core baselith/core`` into an existing
            # directory would nest the tree as ``baselith/core/core``.
            shutil.rmtree(dst)
        if git and _tracked(root, top):
            subprocess.run(
                ["git", "-C", str(root), "mv", top, f"{NEW_ROOT}/{top}"],
                check=True,
                capture_output=True,
            )
        if src.exists():
            dst.mkdir(parents=True, exist_ok=True)
            _merge_into(src, dst)
        moved.append(f"{top}/ -> {NEW_ROOT}/{top}/")
    return moved


__all__ = ["move_tree", "pending_moves"]
