"""Remove overlay entries the bundled plugins have overtaken.

After an image or checkout upgrade the bundled plugin may be as new as, or
newer than, what the overlay holds. Registration already ignores such entries
(:mod:`core.plugins._overlay_guard`); this removes them so they cannot come
back after a bundled downgrade. Called by the update engine (plan-2 step 2),
never at boot: the web process does not change code.

Only symlinks and ``.store`` directories are removed. A plain directory at the
overlay root was put there by an operator and is only reported.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from core.plugins._overlay_guard import bundled_version, read_manifest_mapping
from core.plugins.overlay import STORE_DIRNAME, candidate_overlay_dirs

logger = logging.getLogger(__name__)


def _newer(version: str, bundled: str) -> bool:
    """True when ``version`` beats ``bundled``; unparseable counts as newer (kept)."""
    from core.plugins.version import SemanticVersion

    try:
        return SemanticVersion(version) > SemanticVersion(bundled)
    except ValueError:
        return True


def _stale_links(root: Path, bundled_root: Path) -> list[Path]:
    stale: list[Path] = []
    for entry in candidate_overlay_dirs(root):
        bundled = bundled_version(bundled_root, entry.name)
        if bundled is None:
            continue
        data = read_manifest_mapping(entry.resolve()) or {}
        if _newer(str(data.get("version") or ""), bundled):
            continue
        if not entry.is_symlink():
            logger.warning(
                "Overlay entry %s is not newer than the bundled plugin but is a "
                "plain directory; left in place.",
                entry,
            )
            continue
        stale.append(entry)
    return stale


def prune_stale_overlay(root: Path, bundled_root: Path) -> list[Path]:
    """Remove overlay links and store dirs not newer than the bundled plugin.

    Args:
        root: The overlay directory.
        bundled_root: The bundled plugins tree.

    Returns:
        The removed paths (links first, then store directories).
    """
    removed: list[Path] = []
    for link in _stale_links(root, bundled_root):
        link.unlink()
        removed.append(link)
    store = root / STORE_DIRNAME
    if not store.is_dir():
        return removed
    live = {e.resolve() for e in root.iterdir() if e.is_symlink()}
    for candidate in sorted(store.iterdir()):
        name, sep, version = candidate.name.rpartition("-")
        if not sep or candidate.is_symlink() or not candidate.is_dir():
            continue
        if candidate.resolve() in live:
            continue
        bundled = bundled_version(bundled_root, name)
        if bundled is None or _newer(version, bundled):
            continue
        shutil.rmtree(candidate)
        removed.append(candidate)
    return removed


__all__ = ["prune_stale_overlay"]
