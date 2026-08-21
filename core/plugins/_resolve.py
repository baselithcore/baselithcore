"""Plugin identifier validation and dependency ordering.

Split out of :mod:`core.plugins.loader` (500-line module cap). The name check
lives here because two different entry points need it — the loader resolving a
directory and the resource analyzer reading a manifest — and both join a
caller-supplied identifier onto the plugins root.
"""

from __future__ import annotations

import graphlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .interface import Plugin

#: A plugin identifier is a filesystem-safe slug: it names a directory under
#: the plugins root, so anything that could climb out of it (path separators,
#: ``..``, NUL, leading dots) is rejected rather than normalised away.
PLUGIN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def is_valid_plugin_name(name: str) -> bool:
    """Whether ``name`` is a safe plugin identifier."""
    return bool(PLUGIN_NAME_RE.fullmatch(name)) and ".." not in name


def safe_plugin_path(base: Path, name: str) -> Path:
    """Join ``name`` onto ``base``, refusing anything that escapes ``base``.

    Args:
        base: The plugins root directory.
        name: Caller-supplied plugin identifier (HTTP path parameter, CLI
            argument, manifest field).

    Returns:
        Path: ``base / name``.

    Raises:
        ValueError: If ``name`` is not a valid identifier, or if the joined
            path resolves outside ``base`` (symlink or traversal).
    """
    if not is_valid_plugin_name(name):
        raise ValueError(f"Invalid plugin name: {name!r}")
    # normpath/realpath + prefix check rather than pathlib's is_relative_to:
    # same guarantee, and it is the containment idiom static analysers model,
    # so downstream reads are not each reported as a traversal sink.
    root = os.path.realpath(base)
    candidate = os.path.realpath(os.path.join(root, name))
    if not candidate.startswith(root + os.sep):
        raise ValueError(f"Plugin path escapes the plugins root: {name!r}")
    return Path(candidate)


def sort_by_dependencies(plugins: dict[str, Plugin]) -> list[str]:
    """Order plugin names so dependencies load first (topological sort).

    Dependencies that are not present in ``plugins`` are ignored, so optional
    or external ones do not block the ordering.

    Raises:
        graphlib.CycleError: If the dependency graph contains a cycle.
    """
    graph = {
        name: {d for d in plugin.metadata.dependencies if d in plugins}
        for name, plugin in plugins.items()
    }
    return list(graphlib.TopologicalSorter(graph).static_order())
