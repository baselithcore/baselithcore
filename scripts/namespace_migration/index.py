"""What exists under the legacy packages, so a dotted string can be resolved.

A string such as ``"core.services.llm.factory.build"`` is only rewritten when
the tree says it names something real: a module, a package or a directory.
The index is built from the file list, before or after the move — a tree whose
``core/`` already sits at ``baselith/core/`` resolves the same names, which is
what keeps a second run a no-op instead of a guess.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .config import NEW_ROOT, OLD_TOPS


@dataclass
class ModuleIndex:
    """Dotted names and directory paths that exist under the legacy tops.

    Attributes:
        modules: Dotted names (``core.a.b``) of every module, package and
            directory below a legacy top, both ``.py`` stems and directories.
        paths: Repo-relative paths, in legacy spelling (``core/a/b.py``,
            ``core/a``), of every file and directory below a legacy top.
        nested_paths: Files and directories at or below a ``core``/``plugins``
            directory that is *not* a legacy top (``mkdocs-site/docs/plugins``),
            so a document-relative link into one is recognised as local.
        moved: Whether the tree is already in the new layout.
    """

    modules: set[str] = field(default_factory=set)
    paths: set[str] = field(default_factory=set)
    nested_paths: set[str] = field(default_factory=set)
    moved: bool = False

    @classmethod
    def from_files(cls, files: Iterable[str]) -> ModuleIndex:
        """Build the index from repo-relative POSIX file paths."""
        index = cls()
        saw_old = saw_new = False
        for rel in files:
            parts = PurePosixPath(rel).parts
            if len(parts) >= 2 and parts[0] in OLD_TOPS:
                saw_old = True
                legacy = parts
            elif len(parts) >= 3 and parts[0] == NEW_ROOT and parts[1] in OLD_TOPS:
                saw_new = True
                legacy = parts[1:]
            else:
                index._add_nested(parts)
                continue
            index._add(legacy)
        index.moved = saw_new and not saw_old
        return index

    def _add_nested(self, parts: tuple[str, ...]) -> None:
        for i, part in enumerate(parts[:-1]):
            if part in OLD_TOPS and i > 0 and parts[0] != NEW_ROOT:
                for depth in range(i + 1, len(parts) + 1):
                    self.nested_paths.add("/".join(parts[:depth]))
                return

    def _add(self, legacy: tuple[str, ...]) -> None:
        for depth in range(1, len(legacy)):
            self.paths.add("/".join(legacy[:depth]))
            self.modules.add(".".join(legacy[:depth]))
        self.paths.add("/".join(legacy))
        leaf = legacy[-1]
        if leaf.endswith((".py", ".pyi")):
            stem = leaf.rsplit(".", 1)[0]
            if stem != "__init__":
                self.modules.add(".".join((*legacy[:-1], stem)))

    def resolves(self, dotted: str) -> bool:
        """Whether *dotted* (``core.a.b.Attr``) names something in the tree.

        The longest module prefix must cover at least ``<top>.<first>``; what
        follows it is taken as attribute access (``core.a.module.Class.meth``).
        """
        parts = dotted.split(".")
        if len(parts) < 2 or parts[0] not in OLD_TOPS:
            return False
        return ".".join(parts[:2]) in self.modules

    def path_exists(self, legacy_path: str) -> bool:
        """Whether a legacy-spelled path (``core/a/b.py``) exists."""
        return legacy_path.rstrip("/") in self.paths

    def first_segment_exists(self, top: str, segment: str) -> bool:
        """Whether ``<top>/<segment>`` exists (file or directory)."""
        return f"{top}/{segment}" in self.paths


__all__ = ["ModuleIndex"]
