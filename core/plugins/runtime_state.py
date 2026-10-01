"""Runtime state inside a plugin tree: what a plugin declares, what the engine checks.

State belongs in :func:`core.plugins.plugin_data.data_dir`. A plugin that
cannot move yet declares the paths inside its tree that hold state in the
manifest's ``runtime_state_paths``; the update engine carries those over to
the new version. Anything else in an installed tree must be a file the
installed release shipped: :func:`undeclared_runtime_files` lists the rest, and
the engine refuses to replace a tree while that list is non-empty, because
those files would be lost.

A declared path may never name the manifest, a file the integrity hash covers,
or a code/asset root (:data:`CODE_ASSET_ROOTS`), nor sit inside or contain one —
carrying such a tree over would let old signed code ride into a newly verified
version. The engine never carries over a file the new release ships, and
re-verifies integrity after carry-over.

A plugin-scoped ``.env`` is operator config: it is never reported and the
engine always carries it over (:data:`ALWAYS_CARRIED`). ``node_modules``
directories are build artifacts, ignored like ``__pycache__``.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path, PurePosixPath

import yaml

from core.plugins.integrity import find_manifest_file, is_hashed_path, is_manifest_path

MAX_RUNTIME_STATE_PATHS = 32
_GLOB_CHARS = frozenset("*?[]")
#: Plugin-root files the engine always carries over; never "undeclared".
ALWAYS_CARRIED: tuple[str, ...] = (".env",)
#: Code/asset trees a declaration may not equal, sit inside or contain.
CODE_ASSET_ROOTS: tuple[str, ...] = (
    "ui",
    "ui/dist",
    "ui/out",
    "ui/build",
    "static",
    "frontend",
    "skills",
    "templates",
    "locales",
)
#: Written by the interpreter at import time; never state, never shipped.
_IGNORED_DIRS = frozenset({"__pycache__", "node_modules"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo"})


def _refuse(raw: object, why: str) -> ValueError:
    return ValueError(f"runtime_state_paths entry {raw!r} {why}")


def normalize_runtime_state_paths(value: Iterable[object] | None) -> list[str]:
    """Validate and normalise declared runtime-state paths.

    Args:
        value: The manifest's list, or None.

    Returns:
        Relative POSIX paths without a trailing slash, first occurrence kept.

    Raises:
        ValueError: An entry is not a string, is empty, absolute, contains
            ``.``/``..``/empty segments, a backslash or a glob, names the
            manifest or a hashed file, or there are too many entries.
    """
    if value is None:
        return []
    out: list[str] = []
    for raw in value:
        if not isinstance(raw, str):
            raise _refuse(raw, "must be a string")
        text = raw.strip().rstrip("/")
        if not text:
            raise _refuse(raw, "must not be empty")
        if "\\" in text or any(ch in _GLOB_CHARS for ch in text):
            raise _refuse(raw, "must not contain a backslash or a glob")
        if text.startswith("/") or any(p in ("", ".", "..") for p in text.split("/")):
            raise _refuse(raw, "must be a relative path inside the plugin")
        path = PurePosixPath(text)
        if path.parts[0] in _IGNORED_DIRS:
            raise _refuse(raw, "names an interpreter cache or build artifact")
        for root in CODE_ASSET_ROOTS:
            if (
                text == root
                or text.startswith(root + "/")
                or root.startswith(text + "/")
            ):
                raise _refuse(raw, f"overlaps the code/asset root {root!r}")
        if is_manifest_path(Path(text)) or is_hashed_path(Path(text)):
            raise _refuse(raw, "names the manifest or a file the integrity hash covers")
        if text not in out:
            out.append(text)
    if len(out) > MAX_RUNTIME_STATE_PATHS:
        raise ValueError(
            f"runtime_state_paths declares {len(out)} entries; at most "
            f"{MAX_RUNTIME_STATE_PATHS} are allowed"
        )
    return out


def declared_runtime_state_paths(plugin_dir: Path) -> list[str]:
    """The normalised ``runtime_state_paths`` of the plugin in ``plugin_dir``.

    Returns:
        ``[]`` when there is no manifest or no declaration.

    Raises:
        ValueError: The declaration is malformed (see
            :func:`normalize_runtime_state_paths`).
    """
    manifest = find_manifest_file(plugin_dir)
    if manifest is None:
        return []
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    raw = data.get("runtime_state_paths") if isinstance(data, dict) else None
    if isinstance(raw, str):
        raw = [raw]
    if raw is not None and not isinstance(raw, list):
        raise ValueError("runtime_state_paths must be a list of paths")
    return normalize_runtime_state_paths(raw)


def _declared(rel: str, declared: Sequence[str]) -> bool:
    return any(rel == p or rel.startswith(p + "/") for p in declared)


def undeclared_runtime_files(
    plugin_dir: Path,
    known_files: Collection[str],
    runtime_state_paths: Sequence[str],
) -> list[str]:
    """Files in an installed tree that neither shipped nor are declared state.

    Symlinks are reported as entries (never followed); ``__pycache__`` and
    ``.pyc``/``.pyo`` files are ignored.

    Args:
        plugin_dir: The installed plugin tree.
        known_files: Relative POSIX paths the installed release shipped.
        runtime_state_paths: Normalised declared paths.

    Returns:
        Sorted relative POSIX paths; empty when the tree may be replaced.

    Raises:
        OSError: A directory cannot be read; the check fails closed rather than
            miss state it could not see.
    """
    known = set(known_files)
    found: list[str] = []

    def _fail(error: OSError) -> None:
        raise OSError(
            error.errno, f"cannot inspect {error.filename}: {error.strerror}"
        ) from error

    for current, dirnames, filenames in os.walk(
        plugin_dir, followlinks=False, onerror=_fail
    ):
        base = Path(current)
        links = [d for d in dirnames if (base / d).is_symlink()]
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and d not in links]
        for name in [*filenames, *links]:
            path = base / name
            if path.suffix in _IGNORED_SUFFIXES:
                continue
            rel = path.relative_to(plugin_dir).as_posix()
            if (
                rel in ALWAYS_CARRIED
                or rel in known
                or _declared(rel, runtime_state_paths)
            ):
                continue
            found.append(rel)
    return sorted(found)


__all__ = [
    "ALWAYS_CARRIED",
    "CODE_ASSET_ROOTS",
    "MAX_RUNTIME_STATE_PATHS",
    "declared_runtime_state_paths",
    "normalize_runtime_state_paths",
    "undeclared_runtime_files",
]
