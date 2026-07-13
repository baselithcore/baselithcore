"""
Synthetic parent-package registration for plugin module loading.

Shared by the async :class:`core.plugins.loader.PluginLoader` and the sync
pre-discovery in :mod:`core.plugins.app_setup` (previously duplicated in
both). Registering ``plugins`` / ``plugins.<name>`` parents in
``sys.modules`` lets relative imports inside a plugin resolve correctly and
keeps ``__package__ == __spec__.parent`` (avoids DeprecationWarning).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _install_reexport_hook(
    stub: types.ModuleType, pkg_fqn: str, plugin_dir: Path, init_py: Path
) -> None:
    """Materialise the real package ``__init__.py`` on first top-level access.

    A plugin loaded via its ``plugin.py`` (fqn ``plugins.<name>.plugin``) needs
    ``plugins.<name>`` present so *relative* imports resolve against
    ``__path__`` — but its re-exporting ``__init__.py`` is never executed by
    that path. If the ``__init__.py`` re-exports symbols consumed elsewhere
    (e.g. ``document_sources`` → ``DocumentSourceError`` /
    ``create_document_sources``, imported by the ``core.doc_sources`` shim), the
    bare stub SHADOWS them permanently: ``from plugins.<name> import <symbol>``
    then fails with the opaque
    ``cannot import name '<symbol>' from 'plugins.<name>' (unknown location)``
    (``unknown location`` == the stub carries no ``__file__``).

    A PEP 562 module ``__getattr__`` fixes this without changing the load path:
    relative *submodule* imports read ``__path__`` (a real attribute) and never
    reach the hook, so plugin loading stays byte-for-byte as before; only a
    genuine missing top-level attribute (a real ``from plugins.<name> import
    <symbol>``) triggers a one-shot exec of the real ``__init__.py`` *into the
    stub*, binding the re-exports and a proper ``__file__`` while preserving
    module identity. Deferring to first access also keeps pre-discovery — which
    scans every plugin dir — free of ``__init__`` side effects.
    """
    state = {"done": False}

    def __getattr__(name: str) -> object:
        if state["done"]:
            raise AttributeError(name)
        # A relative submodule import (``from . import sub`` / ``from .sub
        # import x``) makes CPython's ``_handle_fromlist`` probe
        # ``hasattr(parent, 'sub')`` BEFORE the submodule is imported — and on
        # this stub ``hasattr`` reaches ``__getattr__``. That probe must NOT
        # trigger the one-shot ``__init__.py`` exec: running the re-exporting
        # ``__init__`` (``from .plugin import XPlugin``) while ``plugin.py`` is
        # only half-executed re-enters it before the class is defined →
        # ``ImportError: cannot import name 'XPlugin'``. Defer to the normal
        # import machinery for anything that is a real submodule on disk, and
        # never materialise ``__init__`` for a dunder probe (``__all__`` etc.);
        # in both cases leave the one-shot unconsumed so a genuine re-export
        # access still works later.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        if (plugin_dir / f"{name}.py").exists() or (
            plugin_dir / name / "__init__.py"
        ).exists():
            raise AttributeError(name)
        state["done"] = True
        spec = importlib.util.spec_from_file_location(
            pkg_fqn, init_py, submodule_search_locations=[str(plugin_dir)]
        )
        if spec is None or spec.loader is None:
            raise AttributeError(name)
        # Drop the hook before exec so an internal miss can't recurse, then run
        # the real __init__.py into the stub (module identity preserved).
        stub.__dict__.pop("__getattr__", None)
        stub.__spec__ = spec
        stub.__loader__ = spec.loader
        stub.__file__ = str(init_py)
        spec.loader.exec_module(stub)
        return getattr(stub, name)

    stub.__getattr__ = __getattr__  # type: ignore[method-assign, attr-defined]


def ensure_parent_packages(plugin_name: str, plugin_dir: Path) -> None:
    """Register synthetic parent packages so __package__ == __spec__.parent."""
    plugins_root = plugin_dir.parent

    if "plugins" not in sys.modules:
        pkg = types.ModuleType("plugins")
        pkg.__path__ = [str(plugins_root)]
        pkg.__package__ = "plugins"
        sys.modules["plugins"] = pkg

    pkg_fqn = f"plugins.{plugin_name}"
    if pkg_fqn in sys.modules:
        return

    pkg = types.ModuleType(pkg_fqn)
    pkg.__path__ = [str(plugin_dir)]
    pkg.__package__ = pkg_fqn
    sys.modules[pkg_fqn] = pkg

    init_py = plugin_dir / "__init__.py"
    if init_py.exists():
        _install_reexport_hook(pkg, pkg_fqn, plugin_dir, init_py)


__all__ = ["ensure_parent_packages"]
