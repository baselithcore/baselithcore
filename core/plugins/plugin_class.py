"""Resolving the ``Plugin`` subclass a plugin module exports.

The loader used to walk ``dir(module)`` and take the first concrete ``Plugin``
subclass it found. ``dir()`` is sorted, so "first" meant *alphabetically first*:
a module defining ``AdminPlugin`` alongside the real ``WidgetPlugin`` booted the
wrong one, silently. Meanwhile the manifest's ``entry_point`` — the field that
exists precisely to say which class is the plugin — was never read.

This module closes both halves:

* ``entry_point: module:Class`` is honoured, resolved relative to the plugin's
  own package;
* without one, the heuristic still applies, but ambiguity is now an error
  rather than an alphabetical coin flip.

Kept out of ``loader.py`` to respect the 500-line module cap.
"""

from __future__ import annotations

import importlib
from types import ModuleType

from .interface import Plugin


class PluginClassError(RuntimeError):
    """Raised when the plugin class cannot be identified unambiguously."""


def _is_candidate(attr: object) -> bool:
    """Whether ``attr`` is a concrete, non-framework plugin class."""
    if not isinstance(attr, type) or not issubclass(attr, Plugin) or attr is Plugin:
        return False
    if getattr(attr, "__abstractmethods__", None):
        return False
    # Framework base classes (``GraphPlugin`` and friends) are concrete and get
    # imported into plugin namespaces; they are never the plugin itself.
    return not (getattr(attr, "__module__", "") or "").startswith("core.plugins")


def _owned_by(cls: type[Plugin], package_prefix: str) -> bool:
    """Whether ``cls`` is defined inside the plugin's own package."""
    module = getattr(cls, "__module__", "") or ""
    return module == package_prefix or module.startswith(f"{package_prefix}.")


def find_plugin_classes(module: ModuleType, package_prefix: str) -> list[type[Plugin]]:
    """Collect the concrete plugin classes a module exposes.

    When the module exposes several, those defined inside the plugin's own
    package win: a plugin that imports a concrete ``Plugin`` subclass from a
    library it depends on is not thereby ambiguous. Ownership is used only to
    *narrow* — a lone candidate is accepted wherever it was defined, which is
    what the previous heuristic did and what plugins in the wild rely on.

    Args:
        module: The executed plugin module.
        package_prefix: The plugin's package FQN (``plugins.<dirname>``).

    Returns:
        Distinct candidate classes, in ``dir()`` (alphabetical) order.
    """
    found: list[type[Plugin]] = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name, None)
        if _is_candidate(attr) and attr not in found:
            found.append(attr)  # type: ignore[arg-type]

    if len(found) > 1:
        owned = [cls for cls in found if _owned_by(cls, package_prefix)]
        if owned:
            return owned
    return found


def _load_entry_point_module(
    module: ModuleType,
    package_prefix: str,
    module_part: str,
) -> ModuleType:
    """Import the module half of an ``entry_point``, relative to the plugin."""
    if not module_part or module_part in (".", module.__name__):
        return module
    # ``plugin:Class`` on a package loaded from ``plugin.py`` already *is* the
    # loaded module; avoid a redundant (and order-sensitive) re-import.
    if module.__name__.endswith(f".{module_part}"):
        return module
    return importlib.import_module(f"{package_prefix}.{module_part}")


def resolve_plugin_class(
    module: ModuleType,
    *,
    package_prefix: str,
    entry_point: str = "",
) -> type[Plugin]:
    """Identify the plugin class for a freshly executed plugin module.

    Args:
        module: The executed plugin module.
        package_prefix: The plugin's package FQN (``plugins.<dirname>``).
        entry_point: The manifest's ``entry_point``, as ``module:Class``,
            ``:Class`` or a bare ``Class``. Empty falls back to the heuristic.

    Returns:
        The plugin class to instantiate.

    Raises:
        PluginClassError: The entry point does not resolve to a concrete
            ``Plugin`` subclass, or — without an entry point — the module
            exposes zero or more than one candidate.
    """
    declared = (entry_point or "").strip()
    if declared:
        module_part, _, class_name = declared.rpartition(":")
        class_name = class_name.strip()
        if not class_name:
            raise PluginClassError(
                f"entry_point '{declared}' names no class; expected 'module:Class'"
            )
        try:
            target = _load_entry_point_module(
                module, package_prefix, module_part.strip()
            )
        except Exception as exc:
            raise PluginClassError(
                f"entry_point '{declared}': could not import module: {exc}"
            ) from exc

        attr = getattr(target, class_name, None)
        if attr is None:
            raise PluginClassError(
                f"entry_point '{declared}': {target.__name__} has no '{class_name}'"
            )
        if not isinstance(attr, type) or not issubclass(attr, Plugin):
            raise PluginClassError(
                f"entry_point '{declared}': '{class_name}' is not a Plugin subclass"
            )
        if getattr(attr, "__abstractmethods__", None):
            raise PluginClassError(
                f"entry_point '{declared}': '{class_name}' is abstract"
            )
        return attr

    candidates = find_plugin_classes(module, package_prefix)
    if not candidates:
        raise PluginClassError("no concrete Plugin subclass found in module")
    if len(candidates) > 1:
        names = ", ".join(sorted(cls.__name__ for cls in candidates))
        raise PluginClassError(
            f"module exposes more than one Plugin subclass ({names}); "
            "declare entry_point: 'module:Class' in the manifest to disambiguate"
        )
    return candidates[0]


__all__ = ["PluginClassError", "find_plugin_classes", "resolve_plugin_class"]
