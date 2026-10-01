"""Symlink detection shared by the overlay loader and the release verifier.

``compute_plugin_hash`` walks a tree without following links, so anything
behind a symlink inside a plugin is unhashed yet importable. Both the overlay
(:mod:`core.plugins.overlay`) and the release verifier
(:mod:`core.plugin_updates.verifier`) refuse such a tree. Stdlib only: the
overlay runs before any plugin import and must stay light.
"""

from __future__ import annotations

import os
from pathlib import Path


def first_symlink(root: Path) -> str | None:
    """Relative path of the first symlink under ``root`` (links never followed).

    ``root`` itself is not inspected, only what lies below it.

    Args:
        root: Directory to walk.

    Returns:
        The path of the first symlink found, relative to ``root``, or None.
    """
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        for name in sorted(dirnames + filenames):
            path = Path(current) / name
            if path.is_symlink():
                return str(path.relative_to(root))
    return None


__all__ = ["first_symlink"]
