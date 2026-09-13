"""Plugin loader for discovering and loading plugins from filesystem."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger
from core.utils.logsafe import sanitize_log_value

from ._audit import audit_plugin_load
from ._env import apply_plugin_env
from ._module_paths import ensure_parent_packages as _ensure_parent_packages
from ._resolve import safe_plugin_path, sort_by_dependencies
from .bulk_load import load_all_plugins as _load_all_plugins
from .discovery import (
    find_manifest,
    iter_entry_point_plugin_dirs,
    merge_plugin_dirs,
)
from .integrity import verify_plugin_integrity
from .interface import Plugin
from .load_gates import config_gate
from .manifest_model import describe_manifest_failure
from .plugin_class import PluginClassError, resolve_plugin_class
from .registry import PluginRegistry
from .resource_analyzer import ResourceAnalyzer

logger = get_logger(__name__)


class PluginLoader:
    """
    Discovers and loads plugins from the filesystem.

    The loader scans a plugins directory, imports plugin modules,
    and registers them with the plugin registry.

    Phase 2 Enhancement: Integrated with PluginLifecycleManager for state tracking.
    """

    def __init__(
        self,
        plugins_dir: Path,
        registry: PluginRegistry,
        lifecycle_manager: Any | None = None,
    ):
        """
        Initialize plugin loader.

        Args:
            plugins_dir: Directory containing plugin packages
            registry: Plugin registry to register loaded plugins
            lifecycle_manager: Optional lifecycle manager for state tracking
        """
        self.plugins_dir = Path(plugins_dir)
        self.registry = registry
        self.lifecycle_manager = lifecycle_manager
        self._loaded_modules: dict[str, Any] = {}
        self._module_packages: dict[str, str] = {}
        self._resource_analyzer = ResourceAnalyzer(self.plugins_dir)
        self._discover_cache: list[Path] | None = None

    @property
    def resource_analyzer(self) -> ResourceAnalyzer:
        """The analyzer this loader reads manifests and capabilities through."""
        return self._resource_analyzer

    def match_config_key(
        self,
        configs: dict[str, dict[str, Any]],
        directory_name: str,
        plugin_name: str,
    ) -> str | None:
        """Resolve which config entry (if any) governs a plugin.

        Config files key plugins by directory name or by manifest name, and the
        two differ for several shipped plugins (``browser_agent`` vs
        ``browser-agent``).

        Args:
            configs: The plugin config mapping.
            directory_name: The plugin's directory name.
            plugin_name: The plugin's manifest name.

        Returns:
            The matching config key, or ``None`` when the plugin is not listed.
        """
        return self._resource_analyzer._match_config_key(
            configs, directory_name, plugin_name
        )

    def invalidate_discovery_cache(self) -> None:
        """Drop the cached plugin directory listing.

        Call after creating, removing, or hot-reloading plugin directories so
        the next ``discover_plugins`` call re-walks the filesystem.
        """
        self._discover_cache = None

    def discover_plugins(self) -> list[Path]:
        """
        Discover plugin directories.

        Two sources are merged: the filesystem scan of ``plugins_dir`` and the
        ``baselith.plugins`` entry-point group advertised by installed
        distributions (see :mod:`core.plugins.discovery`). The directory scan
        wins on a name clash — a tree an operator dropped in locally is a
        deliberate override and must not be shadowed by a wheel on ``sys.path``.

        Returns:
            List of paths to plugin directories
        """
        if self._discover_cache is not None:
            return self._discover_cache

        plugin_dirs = self._scan_plugin_dirs()
        self._discover_cache = merge_plugin_dirs(
            plugin_dirs, iter_entry_point_plugin_dirs()
        )
        return self._discover_cache

    def _scan_plugin_dirs(self) -> list[Path]:
        """Walk ``plugins_dir`` for directories that look like plugins."""
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return []

        plugins_root = self.plugins_dir.resolve()
        plugin_dirs: list[Path] = []
        for item in self.plugins_dir.iterdir():
            # Reject symlinks and paths that escape the plugins directory
            if item.is_symlink() or not item.resolve().is_relative_to(plugins_root):
                logger.warning(f"Skipping suspicious plugin path: {item}")
                continue
            if item.is_dir() and not item.name.startswith((".", "_")):
                # Check if it has a plugin.py or __init__.py
                if (item / "plugin.py").exists() or (item / "__init__.py").exists():
                    plugin_dirs.append(item)
                    logger.debug(f"Discovered plugin directory: {item.name}")

        return plugin_dirs

    async def load_all_plugins(
        self,
        configs: dict[str, dict[str, Any]] | None = None,
        *,
        activate_on_load: bool = True,
    ) -> int:
        """
        Discover and load all plugins with dependency resolution.

        Args:
            configs: Dictionary mapping plugin names to their configurations
            activate_on_load: Initialize each plugin immediately instead of
                registering it cold for lazy activation.

        Returns:
            Number of successfully loaded plugins
        """
        return await _load_all_plugins(self, configs, activate_on_load=activate_on_load)

    async def load_plugin(
        self,
        plugin_dir: Path,
        config: dict[str, Any] | None = None,
        initialize: bool = True,
    ) -> Plugin | None:
        """
        Load a single plugin from directory.

        Args:
            plugin_dir: Path to plugin directory
            config: Configuration dictionary for the plugin
            initialize: Whether to initialize the plugin immediately

        Returns:
            Loaded plugin instance or None if loading failed
        """
        discovery = self._resource_analyzer.discover_plugin(plugin_dir)
        plugin_name = discovery.name if discovery else plugin_dir.name
        # Manifest-supplied, so untrusted: log the escaped name only.
        safe_name = sanitize_log_value(plugin_name)
        package_name = plugin_dir.name
        config = config or {}

        # Track loading state if lifecycle manager available
        if self.lifecycle_manager:
            await self.lifecycle_manager.transition_to_loading(plugin_name)

        # A manifest that is *present but invalid* is refused in every
        # environment. Loading it anyway would run the plugin with
        # ``discovery=None``, which silently means: no declared permissions (so
        # `declared=False` and the capability guards never deny anything), no
        # min_core_version, no declared environment_variables and no
        # entry_point. That is strictly more privilege than the author asked
        # for, granted because their manifest had a typo — the opposite of what
        # the gate is for. A directory with *no* manifest stays allowed: that is
        # the documented legacy shape, and it grants nothing either way.
        if discovery is None:
            manifest_path = find_manifest(plugin_dir)
            if manifest_path is not None:
                reason = describe_manifest_failure(manifest_path)
                logger.error(
                    "Refusing plugin %s: its manifest is present but invalid — %s",
                    safe_name,
                    reason,
                )
                if self.lifecycle_manager:
                    await self.lifecycle_manager.transition_to_failed(
                        plugin_name, ValueError(reason)
                    )
                return None

        try:
            # Verify plugin integrity before executing any of its code.
            # Offloaded to a thread: the check walks the plugin tree and
            # SHA-256-hashes every source file, and load_plugin also runs at
            # runtime via hot-reload — doing that inline would stall the
            # event loop (and every in-flight request) for the whole walk.
            expected_hash = discovery.metadata.integrity_sha256 if discovery else None
            integrity_ok = await asyncio.to_thread(
                verify_plugin_integrity, plugin_dir, expected_hash
            )
            if not integrity_ok:
                logger.error(f"Refusing plugin {safe_name}: integrity check failed")
                return None

            # Publisher-authenticity gate (BASELITH_REQUIRE_PLUGIN_SIGNATURES):
            # the integrity hash proves the tree matches the manifest; the
            # Ed25519 signature proves WHO published it. No-op unless enabled.
            from .signing import enforce_plugin_signature

            signature = discovery.metadata.signature_ed25519 if discovery else None
            if not enforce_plugin_signature(plugin_name, expected_hash, signature):
                return None

            # Look for a plugin-specific .env file. Loaded only after the
            # integrity check passes so an untrusted plugin directory cannot
            # inject environment variables into the process. What it may then
            # set is decided by the shared policy in core.plugins._env: only the
            # plugin's own <DIRNAME>_ namespace (plus the exact keys its manifest
            # declares) reaches os.environ, and framework-global controls are
            # refused outright. An allowlist rather than a denylist, because
            # .env sits outside the integrity-hashed surface and no denylist can
            # enumerate every process-wide variable the venv's libraries read.
            plugin_env = plugin_dir / ".env"
            if plugin_env.exists() and not plugin_env.is_symlink():
                declared_env = tuple(
                    discovery.metadata.environment_variables if discovery else ()
                )
                apply_plugin_env(
                    plugin_env,
                    plugin_name,
                    config,
                    plugin_dir_name=package_name,
                    declared_env_keys=declared_env,
                )

            # Try to import plugin.py first, then fall back to __init__.py
            plugin_file = plugin_dir / "plugin.py"
            if not plugin_file.exists():
                plugin_file = plugin_dir / "__init__.py"

            if not plugin_file.exists():
                logger.error(f"No plugin.py or __init__.py found in {plugin_dir}")
                return None

            # Ensure parent packages exist in sys.modules so relative
            # imports inside the plugin resolve correctly and
            # __package__ == __spec__.parent (avoids DeprecationWarning).
            _ensure_parent_packages(package_name, plugin_dir)

            # For __init__.py the module *is* the package; for plugin.py
            # the module lives *inside* the package.
            if plugin_file.name == "__init__.py":
                module_fqn = f"plugins.{package_name}"
            else:
                module_fqn = f"plugins.{package_name}.plugin"

            spec = importlib.util.spec_from_file_location(
                module_fqn,
                plugin_file,
                submodule_search_locations=(
                    [str(plugin_dir)] if plugin_file.name == "__init__.py" else None
                ),
            )
            if spec is None or spec.loader is None:
                logger.error(f"Failed to create module spec for {safe_name}")
                return None

            module = importlib.util.module_from_spec(spec)
            module.__package__ = f"plugins.{package_name}"
            if plugin_file.name == "__init__.py":
                module.__path__ = [str(plugin_dir)]

            sys.modules[module_fqn] = module
            # Also register under the package name so lookups like
            # `import plugins.{name}` resolve to this module.
            sys.modules.setdefault(f"plugins.{package_name}", module)
            spec.loader.exec_module(module)

            self._loaded_modules[plugin_name] = module
            self._module_packages[plugin_name] = package_name

            # Identify the Plugin class: the manifest's ``entry_point`` when it
            # declares one, otherwise the module's single concrete subclass.
            # Ambiguity is refused rather than resolved alphabetically — see
            # core.plugins.plugin_class.
            try:
                plugin_class = resolve_plugin_class(
                    module,
                    package_prefix=f"plugins.{package_name}",
                    entry_point=(discovery.metadata.entry_point if discovery else ""),
                )
            except PluginClassError as exc:
                logger.error(f"Cannot resolve plugin class for {safe_name}: {exc}")
                return None

            # Instantiate the plugin
            plugin_instance = plugin_class()

            # Track loaded state
            if self.lifecycle_manager:
                await self.lifecycle_manager.transition_to_loaded(
                    plugin_name, plugin_instance
                )

            if initialize:
                # Validate config against the plugin's declared schema before
                # handing it to initialize(), so authors get early feedback.
                if not config_gate(plugin_instance, config):
                    return None

                # Track initializing state
                if self.lifecycle_manager:
                    await self.lifecycle_manager.transition_to_initializing(plugin_name)

                await plugin_instance.initialize(config)

                # Track active state
                if self.lifecycle_manager:
                    await self.lifecycle_manager.transition_to_active(plugin_name)

                meta = plugin_instance.metadata
                safe_meta = sanitize_log_value(f"{meta.name} v{meta.version}")
                logger.info(f"Loaded plugin: {safe_meta}")
                audit_plugin_load(meta.name, version=meta.version, path=str(plugin_dir))

            return plugin_instance

        except Exception as e:
            logger.error(f"Failed to load plugin {safe_name}: {e}", exc_info=True)

            # Track failed state
            if self.lifecycle_manager:
                await self.lifecycle_manager.transition_to_failed(plugin_name, e)

            return None

    def resolve_plugin_dir(self, plugin_name: str) -> Path:
        """Resolve a plugin directory by logical plugin name or filesystem name."""
        # Never join an unvalidated identifier onto the plugins root: a name
        # like "../../etc" would resolve outside it. An unusual-but-legitimate
        # logical name still reaches the registry/discovery lookups below,
        # which match by equality and touch no path.
        try:
            direct_path = safe_plugin_path(self.plugins_dir, plugin_name)
        except ValueError:
            direct_path = None
        if direct_path is not None and direct_path.exists():
            return direct_path

        registry_path = self.registry.get_plugin_directory(plugin_name)
        if registry_path and registry_path.exists():
            return registry_path

        for plugin_dir in self.discover_plugins():
            discovery = self._resource_analyzer.discover_plugin(plugin_dir)
            if discovery and discovery.name == plugin_name:
                return plugin_dir

        raise FileNotFoundError(f"Plugin directory not found for '{plugin_name}'")

    def sort_by_dependencies(self, plugins: dict[str, Plugin]) -> list[str]:
        """Order plugin names so each plugin follows its dependencies.

        Args:
            plugins: Instantiated plugins keyed by manifest name.

        Returns:
            The names in initialization order.

        Raises:
            Exception: Propagated from the topological sort on a dependency
                cycle; the caller decides whether that aborts the whole load.
        """
        return sort_by_dependencies(plugins)

    async def reload_plugin(self, plugin_name: str) -> bool:
        """
        Reload a plugin.

        Args:
            plugin_name: Name of plugin to reload

        Returns:
            True if reload successful, False otherwise
        """
        # Unregister existing plugin
        await self.registry.unregister(plugin_name)

        safe_name = sanitize_log_value(plugin_name)
        # Remove from loaded modules
        if plugin_name in self._loaded_modules:
            self._unload_module(plugin_name)

        # Drop the discovery cache so a freshly added or moved plugin is found.
        self.invalidate_discovery_cache()

        # Reload
        try:
            plugin_dir = self.resolve_plugin_dir(plugin_name)
        except FileNotFoundError:
            logger.error("Plugin directory not found for '%s'", safe_name)
            return False

        plugin = await self.load_plugin(plugin_dir)
        if plugin:
            try:
                self.registry.register(plugin)
                logger.info(f"Reloaded plugin: {safe_name}")
                return True
            except Exception as e:
                logger.error(f"Failed to register reloaded plugin {safe_name}: {e}")
                return False

        return False

    def _unload_module(self, plugin_name: str) -> None:
        """Remove cached import state for a plugin.

        Purges **every** ``sys.modules`` entry under the plugin's package, not
        just ``plugins.<name>`` and ``plugins.<name>.plugin``. Leaving the
        submodules behind meant a reload re-executed only the top module while
        its helpers stayed on the old code — the reload appeared to succeed and
        served a mix of both versions.
        """
        package_name = self._module_packages.get(plugin_name, plugin_name)
        root = f"plugins.{package_name}"
        prefix = f"{root}."
        stale = [
            module_name
            for module_name in sys.modules
            if module_name == root or module_name.startswith(prefix)
        ]
        for module_name in stale:
            sys.modules.pop(module_name, None)

        self._loaded_modules.pop(plugin_name, None)
        self._module_packages.pop(plugin_name, None)

    def get_plugin_info(self, plugin_name: str) -> dict[str, Any] | None:
        """
        Get information about a loaded plugin.

        Args:
            plugin_name: Name of plugin

        Returns:
            Plugin information dictionary or None if not found
        """
        plugin = self.registry.get(plugin_name)
        if plugin:
            return {
                "name": plugin.metadata.name,
                "version": plugin.metadata.version,
                "description": plugin.metadata.description,
                "author": plugin.metadata.author,
                "dependencies": plugin.metadata.dependencies,
                "initialized": plugin.is_initialized(),
                "agents": len(plugin.get_agents()),
                "routers": len(plugin.get_routers()),
                "entity_types": len(plugin.get_entity_types()),
                "relationship_types": len(plugin.get_relationship_types()),
                "intent_patterns": len(plugin.get_intent_patterns()),
                "flow_handlers": len(plugin.get_flow_handlers()),
            }
        return None
