"""The overlay link of a plugin: read it, switch it atomically, prune the store.

Everything here stays inside ``<overlay>/.store`` and the plugin's own link:
store entries are removed only when they are real directories (a symlink in
the store is never followed), names are plain single path components, and the
entry the link points to is never pruned.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Collection
from pathlib import Path

from core.plugins.overlay import STORE_DIRNAME
from core.plugins.version import SemanticVersion

logger = logging.getLogger(__name__)

_STALE_SCRATCH_SECONDS = 86_400


def _plain(name: str) -> bool:
    return (
        bool(name)
        and name not in (".", "..")
        and os.sep not in name
        and "/" not in name
    )


def current_target(overlay_root: Path, plugin: str) -> str | None:
    """Store entry the plugin's link points to; None when unlinked (bundled).

    Raises:
        ValueError: The path is a plain directory, the link leaves ``.store``
            or names another plugin's entry, or it cannot be resolved (a loop).
    """
    if not _plain(plugin):
        raise ValueError(f"invalid plugin name {plugin!r}")
    link = overlay_root / plugin
    if not link.is_symlink():
        if link.exists():
            raise ValueError(f"{link} is not a link; left for the operator")
        return None
    try:
        store = (overlay_root / STORE_DIRNAME).resolve()
        resolved = link.resolve()
    except (RuntimeError, OSError) as exc:  # a symlink loop, an unreadable hop
        raise ValueError(f"{link} cannot be resolved: {exc}") from exc
    if resolved.parent != store or not _plain(resolved.name):
        raise ValueError(f"{link} escapes {STORE_DIRNAME}")
    prefix = f"{plugin}-"
    if not resolved.name.startswith(prefix):
        raise ValueError(f"{link} points at another plugin's entry")
    try:
        SemanticVersion(resolved.name[len(prefix) :])
    except ValueError as exc:  # e.g. demo -> demo-extra-1.0.0
        raise ValueError(f"{link} points at a non-version entry") from exc
    return resolved.name


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def point_to(
    overlay_root: Path, plugin: str, target: str | None, *, run_id: str
) -> None:
    """Point ``<overlay>/<plugin>`` at ``.store/<target>``, or unlink it (None)."""
    current_target(overlay_root, plugin)  # refuses a plain directory or escape
    link = overlay_root / plugin
    if target is None:
        link.unlink(missing_ok=True)
        _fsync_dir(overlay_root)
        return
    if not _plain(target) or not target.startswith(f"{plugin}-"):
        raise ValueError(f"invalid store entry {target!r}")
    entry = overlay_root / STORE_DIRNAME / target
    if entry.is_symlink() or not entry.is_dir():
        raise FileNotFoundError(target)
    if not _plain(run_id):
        raise ValueError(f"invalid run id {run_id!r}")
    tmp = overlay_root / f".{plugin}.tmp-{run_id}"
    tmp.unlink(missing_ok=True)
    os.symlink(os.path.join(STORE_DIRNAME, target), tmp)
    try:
        os.replace(tmp, link)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(overlay_root)


def _stale_scratch(store: Path) -> list[Path]:
    now = time.time()
    stale: list[Path] = []
    for p in [*store.glob(".staging-*"), *store.glob(".trash-*")]:
        try:
            if (
                p.is_symlink()
                or not p.is_dir()
                or now - p.stat().st_mtime <= _STALE_SCRATCH_SECONDS
            ):
                continue
        except FileNotFoundError:
            continue
        stale.append(p)
    return stale


def _entries(store: Path, plugin: str) -> list[tuple[SemanticVersion, Path]]:
    found: list[tuple[SemanticVersion, Path]] = []
    prefix = f"{plugin}-"
    for p in store.iterdir():
        if not p.name.startswith(prefix) or p.is_symlink() or not p.is_dir():
            continue
        try:
            found.append((SemanticVersion(p.name[len(prefix) :]), p))
        except ValueError:
            continue
    return found


def prune_store(
    overlay_root: Path, plugin: str, *, keep: Collection[str], keep_versions: int
) -> list[Path]:
    """Delete old store entries of ``plugin``; never one in ``keep`` or linked.

    Entries are removed oldest semantic version first until at most
    ``keep_versions`` remain (protected entries count toward the total). When
    the plugin's link cannot be read, versions are left alone (fail closed).
    """
    store = overlay_root / STORE_DIRNAME
    if not store.is_dir() or store.is_symlink():
        return []
    removed: list[Path] = []
    for scratch in _stale_scratch(store):
        shutil.rmtree(scratch, ignore_errors=True)
        if not scratch.exists():
            removed.append(scratch)
    try:
        active = current_target(overlay_root, plugin)
    except ValueError as exc:
        logger.warning("Not pruning %s versions: %s", plugin, exc)
        return removed
    protected = set(keep)
    if active is not None:
        protected.add(active)
    entries = sorted(_entries(store, plugin), key=lambda e: e[0], reverse=True)
    kept = sum(1 for _, p in entries if p.name in protected)
    for _, entry in entries:
        if entry.name in protected:
            continue
        if kept < keep_versions:
            kept += 1
            continue
        shutil.rmtree(entry)
        removed.append(entry)
    return removed


__all__ = ["current_target", "point_to", "prune_store"]
