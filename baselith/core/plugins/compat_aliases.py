"""Keep an old module path importable after a module moved inside a plugin.

A plugin that reorganises its modules into sub-packages (``web.py`` →
``sources/web.py``) would otherwise break every documented import of the old
path — including the ``baselith.core.doc_sources`` / ``baselith.core.scraper``
compatibility packages, which *are* the plugin package under another name.
``register_module_aliases`` installs one meta-path finder that answers an
import of ``<package>.<old>`` with the module at ``<package>.<new>``, lazily
(nothing is imported until someone asks) and with a single module identity
(``sys.modules`` holds the same object under both names, so ``isinstance``
and ``monkeypatch`` keep working).

    # baselith/plugins/document_sources/__init__.py
    register_module_aliases(
        {"web": "sources.web", "readers": "readers"},
        packages=(__name__, "baselith.core.doc_sources"),
    )

Only the old names listed are served; everything else goes through the normal
import system, so a typo still raises ``ModuleNotFoundError``.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterable, Sequence
from importlib.abc import Loader, MetaPathFinder
from importlib.machinery import ModuleSpec
from importlib.util import spec_from_loader
from types import ModuleType

_ALIASES: dict[str, str] = {}


class _AliasLoader(Loader):
    """Returns the target module as the module for the aliased name."""

    def __init__(self, target: str) -> None:
        self._target = target

    def create_module(self, spec: ModuleSpec) -> ModuleType:
        return importlib.import_module(self._target)

    def exec_module(
        self, module: ModuleType
    ) -> None:  # already executed by import_module
        return None


class _AliasFinder(MetaPathFinder):
    """One finder for every alias registered in the process."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        new = _ALIASES.get(fullname)
        if new is None or new == fullname:
            return None
        return spec_from_loader(fullname, _AliasLoader(new), origin=f"alias of {new}")


_FINDER = _AliasFinder()


def register_module_aliases(
    aliases: dict[str, str],
    *,
    packages: Iterable[str],
    unchanged: Iterable[str] = (),
) -> None:
    """Serve ``<package>.<old>`` from ``<first package>.<new>`` for every pair.

    Args:
        aliases: ``{old_relative_name: new_relative_name}``; both are dotted paths
            relative to the plugin package (``"web"`` → ``"sources.web"``).
        packages: the package names the aliases are reachable under — the plugin
            package itself and any ``baselith.core`` compatibility name that is
            the same module object.
        unchanged: modules that did *not* move but must still resolve to the
            plugin's own module object under the extra package names
            (``baselith.core.scraper.models`` → ``baselith.plugins.web_scraper.models``);
            without this the import system builds a second copy of the module
            under the compatibility name and ``isinstance`` checks across the two
            names fail.
    """
    packages = tuple(packages)
    if not packages:
        raise ValueError("register_module_aliases needs at least one package name")
    canonical = packages[0]
    for old, new in aliases.items():
        target = f"{canonical}.{new}"
        for package in packages:
            _ALIASES[f"{package}.{old}"] = target
    for name in unchanged:
        for package in packages[1:]:
            _ALIASES[f"{package}.{name}"] = f"{canonical}.{name}"
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)


def registered_aliases() -> dict[str, str]:
    """A copy of the alias table (``old full name`` → ``new full name``)."""
    return dict(_ALIASES)


__all__ = ["register_module_aliases", "registered_aliases"]
