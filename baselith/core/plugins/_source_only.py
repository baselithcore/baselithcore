"""Import plugin code from its source, never from a bytecode cache.

``compute_plugin_hash`` covers a plugin's ``*.py`` files, and the Ed25519
signature covers that hash. CPython, however, executes the ``.pyc`` it finds in
``__pycache__`` whenever the cache *claims* to match the source: a timestamp
pyc is accepted on the source's mtime and size, an unchecked hash-based pyc
(PEP 552) is accepted with no comparison at all, and a sourceless ``mod.pyc``
beside the sources is importable outright. None of those files can be hashed
reproducibly, so a tree whose sources still verify could run bytecode nobody
signed.

The finder below takes every ``baselith.plugins.*`` import off that path: the
module is compiled from the bytes of the ``.py`` file that the hash walk read,
and a module that exists only as bytecode is refused. Compiling the whole
official plugin set from source costs about 40 ms, once per process.

Stdlib only: this is imported by ``baselith.plugins`` before any plugin module.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from importlib.abc import MetaPathFinder
from importlib.machinery import EXTENSION_SUFFIXES, ModuleSpec, SourceFileLoader
from importlib.util import spec_from_file_location
from pathlib import Path
from types import CodeType, ModuleType

PLUGIN_NAMESPACE = "baselith.plugins."


class SourceOnlyLoader(SourceFileLoader):
    """A ``SourceFileLoader`` that neither reads nor writes bytecode caches."""

    def get_code(self, fullname: str) -> CodeType | None:
        """Compile ``fullname`` from its source file.

        Args:
            fullname: Dotted module name.

        Returns:
            The code object compiled from the source bytes on disk.
        """
        path = self.get_filename(fullname)
        code: CodeType = self.source_to_code(self.get_data(path), path)
        return code


class _SourceOnlyFinder(MetaPathFinder):
    """Resolve ``baselith.plugins.*`` submodules to :class:`SourceOnlyLoader`."""

    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Return a source-only spec, or None to defer to the default finders.

        Extension modules and namespace packages are left to the default
        machinery (native code is covered by the hash from surface V3 on).

        Raises:
            ImportError: The module exists only as a ``.pyc`` file.
        """
        if path is None or not fullname.startswith(PLUGIN_NAMESPACE):
            return None
        tail = fullname.rpartition(".")[2]
        for entry in path:
            if not isinstance(entry, str):
                continue
            base = os.path.join(entry, tail)
            init = os.path.join(base, "__init__.py")
            if os.path.isfile(init):
                return spec_from_file_location(
                    fullname,
                    init,
                    loader=SourceOnlyLoader(fullname, init),
                    submodule_search_locations=[base],
                )
            if any(os.path.isfile(base + suffix) for suffix in EXTENSION_SUFFIXES):
                return None
            source = base + ".py"
            if os.path.isfile(source):
                return spec_from_file_location(
                    fullname, source, loader=SourceOnlyLoader(fullname, source)
                )
            if os.path.isfile(base + ".pyc") or os.path.isfile(
                os.path.join(base, "__init__.pyc")
            ):
                raise ImportError(
                    f"{fullname} exists only as bytecode; plugin modules are "
                    "imported from source so that the integrity hash covers "
                    "what runs",
                    name=fullname,
                )
        return None


_FINDER = _SourceOnlyFinder()


def install_source_only_finder() -> None:
    """Put the finder first on ``sys.meta_path`` (idempotent)."""
    if _FINDER not in sys.meta_path:
        sys.meta_path.insert(0, _FINDER)


def plugin_entry_spec(module_fqn: str, plugin_file: Path) -> ModuleSpec | None:
    """Spec for a plugin's entry module (``plugin.py`` or ``__init__.py``).

    Args:
        module_fqn: Name the module is registered under.
        plugin_file: The entry file; an ``__init__.py`` makes the spec a package
            rooted at its directory.

    Returns:
        A spec bound to :class:`SourceOnlyLoader`, with the finder installed so
        the plugin's own submodules resolve the same way.
    """
    install_source_only_finder()
    is_package = plugin_file.name == "__init__.py"
    return spec_from_file_location(
        module_fqn,
        plugin_file,
        loader=SourceOnlyLoader(module_fqn, str(plugin_file)),
        submodule_search_locations=[str(plugin_file.parent)] if is_package else None,
    )


__all__ = [
    "PLUGIN_NAMESPACE",
    "SourceOnlyLoader",
    "install_source_only_finder",
    "plugin_entry_spec",
]
