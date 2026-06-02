"""Synchronous plugin pre-discovery for app-level middleware composition.

The standard plugin loader is async and runs inside the FastAPI lifespan —
i.e. *after* Starlette has already frozen the middleware stack. Plugins that
need to register Starlette middleware (CORS overrides, per-path gates,
telemetry collectors, …) must therefore be discovered earlier, during
``create_app()``.

This module provides a single entry point, :func:`apply_plugin_app_middleware`,
that walks the plugins directory, imports each plugin module enough to
locate its :class:`Plugin` subclass, and invokes the class-level
:meth:`Plugin.setup_app_middleware` hook on the freshly built application.

Design constraints
------------------

* **No static ``plugins.*`` imports.** Discovery uses
  :func:`importlib.util.spec_from_file_location` so the architectural
  boundary checker (which detects ``core -> plugins`` via AST) stays happy.
* **Sync.** ``create_app()`` is sync; this helper must not require an event
  loop.
* **Best-effort.** A failing plugin must not block boot — failures are
  logged and the remaining plugins are still processed.
* **Integrity preserved.** Plugin SHA-256 integrity is enforced before
  ``exec_module`` exactly as the async loader does.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Optional

from core.observability.logging import get_logger

from .integrity import verify_plugin_integrity
from .interface import Plugin
from .resource_analyzer import ResourceAnalyzer

logger = get_logger(__name__)


def _declares_setup_app_middleware(plugin_file: Path) -> bool:
    """Cheap AST scan: True when the plugin module defines the hook.

    We refuse to exec_module a plugin just to discover it doesn't need the
    hook — that would defeat lazy-loading and risk triggering heavy import
    side effects (DB pools, model warmup, …) for nothing.
    """
    try:
        tree = ast.parse(
            plugin_file.read_text(encoding="utf-8"), filename=str(plugin_file)
        )
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "setup_app_middleware":
                return True
    return False


def _ensure_parent_packages(plugin_name: str, plugin_dir: Path) -> None:
    """Mirror the async loader's synthetic-parent setup so relative imports work."""
    plugins_root = plugin_dir.parent

    if "plugins" not in sys.modules:
        pkg = types.ModuleType("plugins")
        pkg.__path__ = [str(plugins_root)]  # type: ignore[attr-defined]
        pkg.__package__ = "plugins"
        sys.modules["plugins"] = pkg

    pkg_fqn = f"plugins.{plugin_name}"
    if pkg_fqn not in sys.modules:
        pkg = types.ModuleType(pkg_fqn)
        pkg.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
        pkg.__package__ = pkg_fqn
        sys.modules[pkg_fqn] = pkg


def _load_plugin_module(plugin_dir: Path) -> Optional[Any]:
    """Exec-load the plugin module enough to read its ``Plugin`` subclass.

    Returns ``None`` when the directory carries no plugin entry point or the
    integrity check fails.
    """
    plugin_file = plugin_dir / "plugin.py"
    if not plugin_file.exists():
        plugin_file = plugin_dir / "__init__.py"
    if not plugin_file.exists():
        return None

    package_name = plugin_dir.name
    _ensure_parent_packages(package_name, plugin_dir)

    module_fqn = (
        f"plugins.{package_name}"
        if plugin_file.name == "__init__.py"
        else f"plugins.{package_name}.plugin"
    )

    # Skip re-exec when the loader already imported it earlier.
    cached = sys.modules.get(module_fqn)
    if cached is not None:
        return cached

    spec = importlib.util.spec_from_file_location(
        module_fqn,
        plugin_file,
        submodule_search_locations=(
            [str(plugin_dir)] if plugin_file.name == "__init__.py" else None
        ),
    )
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    module.__package__ = f"plugins.{package_name}"
    if plugin_file.name == "__init__.py":
        module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
    sys.modules[module_fqn] = module
    # Deliberately do NOT overwrite ``sys.modules['plugins.<name>']`` here:
    # some plugins (e.g. document_sources) ship a re-exporting ``__init__.py``
    # that the rest of the codebase imports symbols from. The async loader
    # gets away with shadowing the package because it owns the full load
    # order; this pre-discovery step runs early and must leave the package
    # ModuleType intact so the canonical loader can re-bind it later.
    spec.loader.exec_module(module)
    return module


def _find_plugin_class(module: Any) -> Optional[type[Plugin]]:
    """Return the first concrete :class:`Plugin` subclass exported by ``module``."""
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, Plugin)
            and attr is not Plugin
            and not getattr(attr, "__abstractmethods__", None)
        ):
            return attr
    return None


def _overrides_setup_app_middleware(plugin_class: type[Plugin]) -> bool:
    """True when the subclass actually overrides the default no-op hook."""
    own = plugin_class.__dict__.get("setup_app_middleware")
    if own is None:
        # Inherited from a mixin? Walk the MRO up to ``Plugin``.
        for base in plugin_class.__mro__[1:]:
            if base is Plugin:
                break
            if "setup_app_middleware" in base.__dict__:
                return True
        return False
    return True


def apply_plugin_app_middleware(app: Any, plugins_dir: Optional[Path] = None) -> int:
    """Discover plugins under ``plugins_dir`` and apply their middleware hooks.

    Args:
        app: The FastAPI application under construction.
        plugins_dir: Override for the plugin root (defaults to ``<repo>/plugins``).

    Returns:
        Count of plugins whose ``setup_app_middleware`` hook ran successfully.
    """
    if plugins_dir is None:
        plugins_dir = Path(__file__).resolve().parents[2] / "plugins"

    if not plugins_dir.exists():
        logger.debug(
            "Plugins directory not found at %s — skipping middleware hook", plugins_dir
        )
        return 0

    analyzer = ResourceAnalyzer(plugins_dir)
    applied = 0

    plugins_root = plugins_dir.resolve()
    for item in plugins_dir.iterdir():
        # Reject symlinks and traversal-style paths, mirroring loader.discover_plugins.
        if item.is_symlink() or not item.resolve().is_relative_to(plugins_root):
            continue
        if not item.is_dir() or item.name.startswith((".", "_")):
            continue
        plugin_file = item / "plugin.py"
        if not plugin_file.exists():
            plugin_file = item / "__init__.py"
        if not plugin_file.exists():
            continue

        # Cheap AST gate — skip plugins that don't even define the hook.
        # Avoids exec_module side effects (DB pools, model warmup, …) for
        # the 90% of plugins that don't need app-level middleware.
        if not _declares_setup_app_middleware(plugin_file):
            continue

        discovery = analyzer.discover_plugin(item)
        expected_hash = discovery.metadata.integrity_sha256 if discovery else None
        if not verify_plugin_integrity(item, expected_hash):
            logger.error(
                "Skipping app-middleware hook for %s: integrity check failed", item.name
            )
            continue

        try:
            module = _load_plugin_module(item)
        except Exception as exc:
            logger.error(
                "Could not load plugin %s for middleware setup: %s",
                item.name,
                exc,
                exc_info=True,
            )
            continue

        if module is None:
            continue

        plugin_class = _find_plugin_class(module)
        if plugin_class is None:
            continue
        if not _overrides_setup_app_middleware(plugin_class):
            continue

        try:
            plugin_class.setup_app_middleware(app)
            applied += 1
            logger.info("🔌 Plugin app-middleware applied: %s", item.name)
        except Exception as exc:
            logger.error(
                "Plugin %s.setup_app_middleware failed: %s",
                plugin_class.__name__,
                exc,
                exc_info=True,
            )

    return applied
