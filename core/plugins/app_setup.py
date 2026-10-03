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
* **Integrity and authenticity preserved.** Both the SHA-256 integrity check
  and the Ed25519 publisher-signature gate run before ``exec_module``, exactly
  as the async loader does.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
import weakref
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger

from ._module_paths import ensure_parent_packages as _ensure_parent_packages
from .config_file import PluginConfigs, plugin_enabled, read_plugin_configs
from .discovery import (
    apply_overlay,
    is_bundled_install_dir,
    iter_entry_point_plugin_dirs,
    merge_plugin_dirs,
    with_bundled_plugins,
)
from .integrity import enforce_signing_policy, verify_plugin_integrity
from .interface import Plugin
from .overlay import registered_overlay_dirs
from .resource_analyzer import ResourceAnalyzer
from .signing import enforce_plugin_signature

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


def _load_plugin_module(plugin_dir: Path) -> Any | None:
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
        module.__path__ = [str(plugin_dir)]
    sys.modules[module_fqn] = module
    # Deliberately do NOT overwrite ``sys.modules['plugins.<name>']`` here:
    # some plugins (e.g. document_sources) ship a re-exporting ``__init__.py``
    # that the rest of the codebase imports symbols from. The async loader
    # gets away with shadowing the package because it owns the full load
    # order; this pre-discovery step runs early and must leave the package
    # ModuleType intact so the canonical loader can re-bind it later.
    spec.loader.exec_module(module)
    return module


def _find_plugin_class(module: Any) -> type[Plugin] | None:
    """Return the first concrete :class:`Plugin` subclass exported by ``module``.

    Framework base classes imported into the plugin's namespace are skipped:
    ``GraphPlugin`` is concrete, so a plugin class whose name sorts after it
    alphabetically (``dir()`` is sorted) would otherwise be shadowed by the
    base and its ``setup_app_middleware`` hook never discovered.
    """
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, Plugin)
            and attr is not Plugin
            and not getattr(attr, "__abstractmethods__", None)
            and not attr.__module__.startswith("core.plugins")
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


_HOOKS_APPLIED: weakref.WeakKeyDictionary[Any, set[str]] = weakref.WeakKeyDictionary()
_RESTART_REQUIRED: weakref.WeakKeyDictionary[Any, set[str]] = (
    weakref.WeakKeyDictionary()
)


def _hook_key(plugin_class: type[Plugin]) -> str:
    """Identity of a plugin class that survives a second import of its module.

    The loader and the pre-discovery import a plugin module separately (two
    class objects), but both under the same ``plugins.<dir>...`` module name,
    so module plus qualified name identifies the plugin. The bare class name
    did not: two plugins each defining ``class Plugin(...)`` collided.
    """
    return f"{plugin_class.__module__}.{plugin_class.__qualname__}"


def _applied_hooks(app: Any) -> set[str]:
    """Keys of plugin classes whose ``setup_app_middleware`` already ran on ``app``."""
    return _HOOKS_APPLIED.setdefault(app, set())


def plugin_restart_required(app: Any, plugin_name: str) -> bool:
    """True when ``plugin_name`` was enabled on ``app`` but needs a restart.

    Set when :func:`apply_late_app_hook` could not add the plugin's
    middleware to an already-started app; the enable API reports it as
    ``restart_required``.
    """
    return plugin_name in _RESTART_REQUIRED.get(app, set())


def apply_late_app_hook(app: Any, plugin: Plugin) -> bool:
    """Run ``setup_app_middleware`` for a plugin enabled after app construction.

    ``create_app()`` skips the hook for plugins the config disables, so a
    runtime enable would otherwise never get its SPA mount. Mounts can be added
    to a running app (the hook is keyed by module and qualified class name,
    since the loader and the pre-discovery import the module separately);
    middleware cannot (Starlette has frozen the stack), which is logged and
    recorded for :func:`plugin_restart_required` instead of raised.

    Returns:
        True when the hook ran now; False if it was absent, already applied
        or could not be applied without a restart.
    """
    plugin_class = type(plugin)
    if not _overrides_setup_app_middleware(plugin_class):
        return False
    applied = _applied_hooks(app)
    key = _hook_key(plugin_class)
    if key in applied:
        return False
    try:
        plugin_class.setup_app_middleware(app)
    except RuntimeError as exc:
        _RESTART_REQUIRED.setdefault(app, set()).add(plugin.metadata.name)
        logger.warning(
            "Plugin %s needs an app restart to finish enabling: %s",
            plugin.metadata.name,
            exc,
        )
        return False
    except Exception as exc:
        logger.error(
            "Plugin %s.setup_app_middleware failed on runtime enable: %s",
            plugin_class.__name__,
            exc,
            exc_info=True,
        )
        return False
    applied.add(key)
    _RESTART_REQUIRED.get(app, set()).discard(plugin.metadata.name)
    logger.info("🔌 Plugin app-middleware applied late: %s", plugin.metadata.name)
    return True


def _scan_bundled(plugins_dir: Path) -> list[Path]:
    """Directory entries under ``plugins_dir``, mirroring the loader's scan.

    Rejects symlinks and traversal-style paths, as ``loader.discover_plugins``
    does. Overlay entries are verified at registration (and are symlinks into
    ``.store`` by design), so they bypass this filter and replace their
    bundled namesake afterwards — same routing as the loader.
    """
    if not plugins_dir.exists():
        logger.debug(
            "Plugins directory not found at %s — scanning entry points only",
            plugins_dir,
        )
        return []
    plugins_root = plugins_dir.resolve()
    return [
        item
        for item in plugins_dir.iterdir()
        if not item.is_symlink() and item.resolve().is_relative_to(plugins_root)
    ]


def apply_plugin_app_middleware(
    app: Any,
    plugins_dir: Path | None = None,
    plugin_configs: PluginConfigs | None = None,
) -> int:
    """Discover plugins under ``plugins_dir`` and apply their middleware hooks.

    Args:
        app: The FastAPI application under construction.
        plugins_dir: Override for the plugin root (defaults to
            ``PLUGIN_PLUGINS_PATH`` as
            :func:`core.config.plugins.resolve_plugins_root` resolves it).
        plugin_configs: The plugin enable-list (``configs/plugins.yaml``
            content). Defaults to reading the file the lifespan reads, so a
            plugin disabled there is skipped here as well.

    Returns:
        Count of plugins whose ``setup_app_middleware`` hook ran successfully.
    """
    if plugins_dir is None:
        from core.config.plugins import get_plugin_config

        # The root the lifespan's loader scans: one resolver for both.
        plugins_dir = Path(get_plugin_config().plugins_path)
    configs = read_plugin_configs() if plugin_configs is None else plugin_configs

    # The same merged set the loader discovers: bundled scan (with verified
    # overlay entries swapped in), then ``baselith.plugins`` entry points.
    candidates = merge_plugin_dirs(
        apply_overlay(
            with_bundled_plugins(plugins_dir, _scan_bundled(plugins_dir)),
            registered_overlay_dirs(),
        ),
        iter_entry_point_plugin_dirs(),
    )
    if not candidates:
        return 0

    # Same posture check the async loader performs: in production, unsigned
    # plugins are refused at verify time by default (fail-closed) unless the
    # explicit BASELITH_ALLOW_UNSIGNED_IN_PROD opt-out is set, which this logs.
    enforce_signing_policy()

    analyzer = ResourceAnalyzer(plugins_dir)
    applied = 0

    for item in candidates:
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
        # The same enable-list the lifespan applies: a plugin the config
        # disables (or omits, when the config names any plugin at all) gets
        # no routers later, so it must get no app-level middleware or SPA
        # mount here either — otherwise a release with a declarative plugin
        # set still serves the console shell of every plugin in the image.
        if not plugin_enabled(
            configs,
            item.name,
            discovery.name if discovery else item.name,
            bundled=is_bundled_install_dir(item),
        ):
            logger.debug("Skipping app-middleware hook for %s: not enabled", item.name)
            continue
        expected_hash = discovery.metadata.integrity_sha256 if discovery else None
        if not verify_plugin_integrity(item, expected_hash):
            logger.error(
                "Skipping app-middleware hook for %s: integrity check failed", item.name
            )
            continue

        # Publisher-authenticity gate, same as the async loader. The integrity
        # hash only proves the tree matches the manifest — an attacker who can
        # write the plugin tree recomputes it — so without this a plugin
        # declaring setup_app_middleware reached exec_module below with
        # BASELITH_REQUIRE_PLUGIN_SIGNATURES entirely bypassed. No-op unless
        # signature enforcement is enabled.
        plugin_name = discovery.name if discovery else item.name
        signature = discovery.metadata.signature_ed25519 if discovery else None
        if not enforce_plugin_signature(plugin_name, expected_hash, signature):
            logger.error(
                "Skipping app-middleware hook for %s: signature check failed", item.name
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
            _applied_hooks(app).add(_hook_key(plugin_class))
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
