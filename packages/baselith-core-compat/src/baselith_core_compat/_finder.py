# ns-migrate: skip-file  (this module maps the legacy names on purpose)
"""Meta-path finder aliasing ``core.*``/``plugins.*`` to ``baselith.*``."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import types
import warnings
from collections.abc import Sequence

#: legacy top-level name -> new fully qualified package.
LEGACY_PREFIXES: dict[str, str] = {
    "core": "baselith.core",
    "plugins": "baselith.plugins",
}

_warned: set[str] = set()


def _target(fullname: str) -> str | None:
    top, _, rest = fullname.partition(".")
    new = LEGACY_PREFIXES.get(top)
    if new is None:
        return None
    return f"{new}.{rest}" if rest else new


def _ours(fullname: str) -> bool:
    """Whether this finder should answer for *fullname*.

    Top level: only when no genuine ``core``/``plugins`` package is on
    ``sys.path`` (a project's own ``core`` app wins). Submodule: only when the
    already-imported legacy top *is* the aliased ``baselith`` package — the
    finder sits first on ``sys.meta_path``, so without this check a real
    ``core`` package's submodules would be hijacked, and with it the standard
    path finder never gets to load ``baselith/core/x.py`` a second time under
    the legacy name.
    """
    top = fullname.partition(".")[0]
    if "." not in fullname:
        return importlib.machinery.PathFinder.find_spec(top) is None
    legacy = sys.modules.get(top)
    return legacy is not None and legacy is sys.modules.get(LEGACY_PREFIXES[top])


class _AliasLoader(importlib.abc.Loader):
    """Hands back the already-imported new module under the legacy name."""

    def __init__(self, target: str) -> None:
        self.target = target

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return importlib.import_module(self.target)

    def exec_module(self, module: types.ModuleType) -> None:
        # The real module is fully initialised already. The import system
        # rebinds __spec__/__loader__ on the shared object; put the real
        # ones back so introspection (and a later reload) sees the truth.
        real = sys.modules.get(self.target)
        if real is not None and real is module:
            spec = getattr(module, "__spec__", None)
            if spec is not None and spec.name != self.target:
                try:
                    module.__spec__ = importlib.util.find_spec(self.target)
                except ValueError:  # a synthetic module never had a spec
                    module.__spec__ = None


class LegacyAliasFinder(importlib.abc.MetaPathFinder):
    """Answer ``core``/``plugins`` imports with the ``baselith`` modules."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: types.ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        new = _target(fullname)
        if new is None:
            return None
        top = fullname.partition(".")[0]
        if not _ours(fullname):
            return None
        loaded = sys.modules.get(new)
        if loaded is not None:
            # Already imported — possibly a synthetic package the plugin
            # loader registered without a ``__spec__`` (``baselith.plugins.<name>``).
            is_package = hasattr(loaded, "__path__")
        else:
            try:
                real_spec = importlib.util.find_spec(new)
            except (ImportError, ValueError):
                return None
            if real_spec is None:
                return None
            is_package = real_spec.submodule_search_locations is not None
        if top not in _warned:
            _warned.add(top)
            warnings.warn(
                f"importing '{top}' is deprecated since baselith-core 1.0; "
                f"use '{LEGACY_PREFIXES[top]}' (aliased by baselith-core-compat)",
                DeprecationWarning,
                stacklevel=2,
            )
        return importlib.machinery.ModuleSpec(
            fullname,
            _AliasLoader(new),
            is_package=is_package,
        )


_FINDER = LegacyAliasFinder()


def install() -> None:
    """Put the finder first on ``sys.meta_path`` (idempotent).

    First, so a legacy submodule is aliased before the standard path finder
    could load the same file a second time under the old name; a project's
    genuine ``core`` package is still left alone (see :func:`_ours`).
    """
    if not any(isinstance(f, LegacyAliasFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _FINDER)


def uninstall() -> None:
    """Remove the finder and forget which warnings were already emitted."""
    sys.meta_path[:] = [
        f for f in sys.meta_path if not isinstance(f, LegacyAliasFinder)
    ]
    _warned.clear()


__all__ = ["LEGACY_PREFIXES", "LegacyAliasFinder", "install", "uninstall"]
