"""Carry the old tree's operator config and declared runtime state into a new one.

Only :data:`~core.plugins.runtime_state.ALWAYS_CARRIED` and the paths the
installed manifest declares are copied, never over a file the new release
ships and never through a symbolic link: a link on the way to (or inside) a
carried path refuses the carry-over rather than copy what it points at, and so
does a file the integrity hash covers (copying it would break the new tree's
signature, dropping it would silently lose it).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Collection, Sequence
from pathlib import Path, PurePosixPath

from core.plugins.integrity import is_hashed_path
from core.plugins.runtime_state import ALWAYS_CARRIED


class CarryError(Exception):
    """A carried path cannot be copied safely (a link, an unreadable file)."""


def _linked_component(base: Path, rel: str) -> str | None:
    """First component of ``rel`` under ``base`` that is a symlink, else None."""
    path = base
    for part in PurePosixPath(rel).parts:
        path = path / part
        if path.is_symlink():
            return path.relative_to(base).as_posix()
    return None


def _copy_tree(src: Path, dst: Path, rel: str, shipped: Collection[str]) -> None:
    for current, dirnames, filenames in os.walk(src, followlinks=False):
        base = Path(current)
        for name in (*dirnames, *filenames):
            if (base / name).is_symlink():
                shown = f"{rel}/{(base / name).relative_to(src).as_posix()}"
                raise CarryError(f"{shown} is a symbolic link")
        out = dst / base.relative_to(src)
        out.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            inner = f"{rel}/{(base / name).relative_to(src).as_posix()}"
            if inner in shipped:
                continue
            if is_hashed_path(Path(inner)):
                # Copying it would move the digest; dropping it would lose it.
                raise CarryError(f"{inner} is a file the integrity hash covers")
            shutil.copy2(base / name, out / name, follow_symlinks=False)


def carry_state(
    current: Path, new: Path, shipped: Collection[str], declared: Sequence[str]
) -> list[str]:
    """Copy ``.env`` and ``declared`` paths from ``current`` into ``new``.

    Args:
        current: The installed plugin tree.
        new: The verified, unpacked new tree (holds no links).
        shipped: Relative POSIX paths the new release ships.
        declared: Normalised ``runtime_state_paths`` of the installed tree.

    Returns:
        The carried top-level paths, in order.

    Raises:
        CarryError: A carried path is, or passes through, a symbolic link, or
            copying it failed.
    """
    carried: list[str] = []
    for rel in (*ALWAYS_CARRIED, *declared):
        link = _linked_component(current, rel)
        if link is not None:
            raise CarryError(f"{link} is a symbolic link")
        src, dst = current / rel, new / rel
        if not src.exists() or rel in shipped or is_hashed_path(Path(rel)):
            continue
        if _linked_component(new, rel) is not None:
            raise CarryError(f"{rel} is a symbolic link in the new tree")
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                _copy_tree(src, dst, rel, shipped)
            elif src.is_file():
                shutil.copy2(src, dst, follow_symlinks=False)
            else:
                raise CarryError(f"{rel} is not a regular file or directory")
        except OSError as exc:
            raise CarryError(f"cannot carry {rel}: {type(exc).__name__}") from exc
        carried.append(rel)
    return carried


__all__ = ["CarryError", "carry_state"]
