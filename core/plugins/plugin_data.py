"""Per-plugin runtime data directories, outside the plugin's code tree.

A plugin update installs the new version side by side
(``<overlay>/.store/<name>-<version>/``) and switches to it, so anything the
old version wrote inside its own directory would stay behind. Runtime state
(embedded databases, uploads, indexes, caches worth keeping) therefore lives
under ``$BASELITH_PLUGIN_DATA_DIR/<name>``, default ``data/plugins/<name>``
relative to the working directory, which no version switch touches.

Stdlib only: plugins call this at import time. The variable is read raw, like
``BASELITH_PLUGIN_OVERLAY_DIR``; ``core.config`` has already loaded the root
``.env`` into the environment by the time any plugin is imported.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DATA_DIR_ENV = "BASELITH_PLUGIN_DATA_DIR"
DEFAULT_DATA_ROOT = Path("data") / "plugins"

_OVERLAY_ENV = "BASELITH_PLUGIN_OVERLAY_DIR"

#: One path segment: a letter or digit, then letters, digits, ``_`` or ``-``.
#: No dot, so ``.`` and ``..`` can never name a directory.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _forbidden_roots() -> list[tuple[str, Path]]:
    """Trees a version switch replaces: the plugins directory and the overlay."""
    roots = [("plugins directory", (Path(__file__).resolve().parents[2] / "plugins"))]
    overlay = os.environ.get(_OVERLAY_ENV, "").strip()
    if overlay:
        roots.append(("plugin overlay", Path(overlay).expanduser().resolve()))
    return roots


def plugin_data_root() -> Path:
    """The absolute directory holding every plugin's data directory.

    Returns:
        ``$BASELITH_PLUGIN_DATA_DIR`` (``~`` expanded) or ``data/plugins``,
        resolved against the current working directory.

    Raises:
        ValueError: The root lies inside the plugins directory or the overlay.
    """
    raw = os.environ.get(DATA_DIR_ENV, "").strip()
    base = Path(raw).expanduser() if raw else DEFAULT_DATA_ROOT
    root = base.resolve()
    for label, forbidden in _forbidden_roots():
        if root == forbidden or forbidden in root.parents:
            raise ValueError(
                f"{DATA_DIR_ENV} resolves to {root}, inside the {label} "
                f"({forbidden}); runtime data must live outside the plugin trees"
            )
    return root


def data_dir(name: str, *, create: bool = True) -> Path:
    """The runtime data directory of plugin ``name``.

    Args:
        name: The plugin name (its directory name).
        create: Create the directory (and parents) when missing.

    Returns:
        ``plugin_data_root() / name``.

    Raises:
        ValueError: ``name`` is not a single safe path segment.
    """
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid plugin name for a data directory: {name!r}")
    path = plugin_data_root() / name
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["DATA_DIR_ENV", "DEFAULT_DATA_ROOT", "data_dir", "plugin_data_root"]
