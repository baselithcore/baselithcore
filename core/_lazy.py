"""Resolve a package's public API on first access, not at import.

A package whose ``__init__`` imports every submodule eagerly makes the cost of
touching *any* of its names the cost of *all* of them. Measured here:
``from core.plugins.result import SkillResult`` — four small Pydantic fields —
loaded 955 modules and took 0.36 s, because importing any submodule of
``core.plugins`` first runs the package ``__init__``, which imports the
registry, the loader, the hot-reload controller, the FastAPI router and the
observability stack. The same shape made ``from core.agent import Agent`` pull
in 3 209 modules by way of ``core.orchestration``.

:pep:`562` module ``__getattr__`` fixes it without changing a single import
site: the package advertises the same ``__all__``, and each name is resolved
from its own submodule the first time it is read, then cached in the package
namespace so later reads are dictionary lookups. This is the same mechanism
numpy, scipy and scikit-learn use for their top-level namespaces.

Two properties are deliberate:

- **The submodule fallback.** Eager imports had a side effect: ``from .loader
  import PluginLoader`` also bound ``core.plugins.loader`` as an attribute of
  the package. Code relying on that would break under a naive lazy loader, so
  a name that is not an exported symbol is tried as a submodule before the
  ``AttributeError`` is raised. Nothing that worked before stops working.
- **``__all__`` stays a literal.** ``scripts/check_public_api.py`` reads it
  with the AST and never imports; a computed ``__all__`` would make the
  package invisible to the public API surface gate.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping
from importlib import import_module
from typing import Any

__all__ = ["lazy_exports"]


def lazy_exports(package: str, exports: Mapping[str, str]) -> Callable[[str], Any]:
    """Build the ``__getattr__`` that resolves ``package``'s API on demand.

    Args:
        package: The package's own ``__name__``.
        exports: ``{exported name: submodule}``, where the submodule is
            relative to ``package``. Use ``"submodule:original"`` when the
            package renames a symbol on the way out, as ``core.plugins`` does
            with ``from .api import router as plugin_management_router``.

    Returns:
        A function to assign to the package's ``__getattr__``.

    Example:
        >>> _EXPORTS = {"PluginLoader": "loader"}
        >>> __getattr__ = lazy_exports(__name__, _EXPORTS)  # doctest: +SKIP
    """

    def __getattr__(name: str) -> Any:
        target = exports.get(name)
        if target is None:
            # Not an exported symbol. It may still be a submodule, which the
            # eager version bound as a side effect of importing it.
            try:
                return import_module(f"{package}.{name}")
            except ImportError:
                raise AttributeError(
                    f"module {package!r} has no attribute {name!r}"
                ) from None
        submodule, _, original = target.partition(":")
        value = getattr(import_module(f"{package}.{submodule}"), original or name)
        setattr(sys.modules[package], name, value)
        return value

    return __getattr__
