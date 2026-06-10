"""Plugin loader for discovering and loading plugins from filesystem."""

import importlib.util
import sys
import types
from pathlib import Path
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv, dotenv_values
from core.observability.logging import get_logger

from .integrity import enforce_signing_policy, verify_plugin_integrity
from .interface import Plugin
from .load_gates import compat_gate, config_gate
from .registry import PluginRegistry
from .resource_analyzer import ResourceAnalyzer

logger = get_logger(__name__)


def _ensure_parent_packages(plugin_name: str, plugin_dir: Path) -> None:
    """Register synthetic parent packages so __package__ == __spec__.parent."""
    plugins_root = plugin_dir.parent

    if "plugins" not in sys.modules:
        pkg = types.ModuleType("plugins")
        pkg.__path__ = [str(plugins_root)]
        pkg.__package__ = "plugins"
        sys.modules["plugins"] = pkg

    pkg_fqn = f"plugins.{plugin_name}"
    if pkg_fqn not in sys.modules:
        pkg = types.ModuleType(pkg_fqn)
        pkg.__path__ = [str(plugin_dir)]
        pkg.__package__ = pkg_fqn
        sys.modules[pkg_fqn] = pkg


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
        lifecycle_manager: Optional[Any] = None,
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
        self._loaded_modules: Dict[str, Any] = {}
        self._module_packages: Dict[str, str] = {}
        self._resource_analyzer = ResourceAnalyzer(self.plugins_dir)
        self._discover_cache: Optional[List[Path]] = None

    def invalidate_discovery_cache(self) -> None:
        """Drop the cached plugin directory listing.

        Call after creating, removing, or hot-reloading plugin directories so
        the next ``discover_plugins`` call re-walks the filesystem.
        """
        self._discover_cache = None

    def discover_plugins(self) -> List[Path]:
        """
        Discover plugin directories.

        Returns:
            List of paths to plugin directories
        """
        if self._discover_cache is not None:
            return self._discover_cache

        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            self._discover_cache = []
            return self._discover_cache

        plugins_root = self.plugins_dir.resolve()
        plugin_dirs: List[Path] = []
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

        self._discover_cache = plugin_dirs
        return plugin_dirs

    async def load_plugin(
        self,
        plugin_dir: Path,
        config: Optional[Dict[str, Any]] = None,
        initialize: bool = True,
    ) -> Optional[Plugin]:
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
        package_name = plugin_dir.name
        config = config or {}

        # Track loading state if lifecycle manager available
        if self.lifecycle_manager:
            await self.lifecycle_manager.transition_to_loading(plugin_name)

        try:
            # Verify plugin integrity before executing any of its code.
            expected_hash = discovery.metadata.integrity_sha256 if discovery else None
            if not verify_plugin_integrity(plugin_dir, expected_hash):
                logger.error(
                    f"Refusing to load plugin {plugin_name}: integrity check failed"
                )
                return None

            # Look for a plugin-specific .env file. Loaded only after the
            # integrity check passes so an untrusted plugin directory cannot
            # inject environment variables into the process.
            plugin_env = plugin_dir / ".env"
            if plugin_env.exists() and not plugin_env.is_symlink():
                # Extend global environment variables without overwriting main ones
                load_dotenv(plugin_env, override=False)
                logger.debug(f"Loaded plugin environment file: {plugin_env}")

                # Merge the plugin environment variables into the plugin config
                env_vars = dotenv_values(plugin_env)
                # Prefer existing configs over .env defaults if already defined.
                # We merge strictly what's not in the config (case-insensitive
                # keys); precompute the lowered key set once instead of
                # rebuilding it per env var (was O(n*m)).
                config_keys_lower = {ck.lower() for ck in config.keys()}
                for k, v in env_vars.items():
                    if k and v is not None:
                        k_lower = k.lower()
                        if k_lower not in config_keys_lower:
                            config[k_lower] = v
                            config_keys_lower.add(k_lower)

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
                logger.error(f"Failed to create module spec for {plugin_name}")
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

            # Find the Plugin class in the module
            plugin_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Plugin)
                    and attr is not Plugin
                    and not getattr(
                        attr, "__abstractmethods__", None
                    )  # Skip abstract classes
                ):
                    plugin_class = attr
                    break

            if plugin_class is None:
                logger.error(f"No Plugin subclass found in {plugin_name}")
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

                logger.info(
                    f"Loaded plugin: {plugin_instance.metadata.name} v{plugin_instance.metadata.version}"
                )

            return plugin_instance

        except Exception as e:
            logger.error(f"Failed to load plugin {plugin_name}: {e}", exc_info=True)

            # Track failed state
            if self.lifecycle_manager:
                await self.lifecycle_manager.transition_to_failed(plugin_name, e)

            return None

    async def load_all_plugins(
        self,
        configs: Optional[Dict[str, Dict[str, Any]]] = None,
        *,
        activate_on_load: bool = True,
    ) -> int:
        """
        Discover and load all plugins with dependency resolution.

        Args:
            configs: Dictionary mapping plugin names to their configurations

        Returns:
            Number of successfully loaded plugins
        """
        configs = configs or {}
        # Surface (and optionally hard-fail on) an insecure signing posture
        # before any plugin code is loaded.
        enforce_signing_policy()
        plugin_dirs = self.discover_plugins()

        if not plugin_dirs:
            logger.info("No plugins found to load")
            return 0

        # Pass 1: Instantiate all plugins to read metadata (without initializing)
        instantiated_plugins: Dict[str, Plugin] = {}

        # If configs are provided, we only load plugins listed there
        filter_by_config = len(configs) > 0

        plugin_configs_by_name: Dict[str, Dict[str, Any]] = {}

        for plugin_dir in plugin_dirs:
            discovery = self._resource_analyzer.discover_plugin(plugin_dir)
            plugin_name = discovery.name if discovery else plugin_dir.name
            config_key = None

            if filter_by_config:
                config_key = self._resource_analyzer._match_config_key(
                    configs,
                    plugin_dir.name,
                    plugin_name,
                )
                if config_key is None:
                    logger.debug(f"Skipping plugin {plugin_name} (not in config)")
                    continue

                plugin_config = configs[config_key]
                if not plugin_config.get("enabled", True):
                    logger.info(f"Skipping disabled plugin {plugin_name}")
                    continue

            # Load without init
            plugin = await self.load_plugin(plugin_dir, initialize=False)
            if plugin:
                instantiated_plugins[plugin.metadata.name] = plugin
                plugin_configs_by_name[plugin.metadata.name] = configs.get(
                    config_key or plugin.metadata.name, {}
                )

        if not instantiated_plugins:
            return 0

        # Map of available plugin name -> version for dependency compat checks.
        available_versions = {
            name: plugin.metadata.version
            for name, plugin in instantiated_plugins.items()
        }

        # Pass 2: Sort by dependencies
        try:
            sorted_names = self._sort_by_dependencies(instantiated_plugins)
        except Exception as e:
            logger.error(f"Dependency resolution failed: {e}")
            return 0

        # Pass 3: Initialize immediately or register for lazy activation
        loaded_count = 0
        for name in sorted_names:
            plugin = instantiated_plugins.get(name)
            if not plugin:
                continue

            try:
                config = plugin_configs_by_name.get(name, {})

                # Gate on version compatibility and config schema. Warn-only by
                # default; skips the plugin when the matching enforcement flag
                # is set (BASELITH_ENFORCE_PLUGIN_COMPAT / _CONFIG).
                if not compat_gate(plugin, available_versions):
                    continue
                if not config_gate(plugin, config):
                    continue

                if activate_on_load:
                    await plugin.initialize(config)
                    self.registry.register(plugin)
                    logger.info(f"Initialized and registered plugin: {name}")
                else:
                    self.registry.register(plugin, require_initialized=False)
                    logger.info(f"Registered plugin for lazy activation: {name}")
                loaded_count += 1

            except Exception as e:
                logger.error(f"Failed to initialize/register plugin {name}: {e}")

        logger.info(f"Loaded {loaded_count}/{len(plugin_dirs)} plugins")
        return loaded_count

    def resolve_plugin_dir(self, plugin_name: str) -> Path:
        """Resolve a plugin directory by logical plugin name or filesystem name."""
        direct_path = self.plugins_dir / plugin_name
        if direct_path.exists():
            return direct_path

        registry_path = self.registry.get_plugin_directory(plugin_name)
        if registry_path and registry_path.exists():
            return registry_path

        for plugin_dir in self.discover_plugins():
            discovery = self._resource_analyzer.discover_plugin(plugin_dir)
            if discovery and discovery.name == plugin_name:
                return plugin_dir

        raise FileNotFoundError(f"Plugin directory not found for '{plugin_name}'")

    def _sort_by_dependencies(self, plugins: Dict[str, Plugin]) -> List[str]:
        """
        Sort plugin names by dependencies using topological sort.

        Args:
            plugins: Dictionary of name -> Plugin instance

        Returns:
            List of plugin names in dependency order
        """
        import graphlib

        # Build graph
        graph = {}
        for name, plugin in plugins.items():
            # Filter dependencies to only those present in the system
            # This allows optional dependencies or external ones to be ignored by the sorter
            deps = {d for d in plugin.metadata.dependencies if d in plugins}
            graph[name] = deps

        ts = graphlib.TopologicalSorter(graph)

        # This will raise CycleError if circular deps exist
        return list(ts.static_order())

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

        # Remove from loaded modules
        if plugin_name in self._loaded_modules:
            self._unload_module(plugin_name)

        # Drop the discovery cache so a freshly added or moved plugin is found.
        self.invalidate_discovery_cache()

        # Reload
        try:
            plugin_dir = self.resolve_plugin_dir(plugin_name)
        except FileNotFoundError:
            logger.error("Plugin directory not found for '%s'", plugin_name)
            return False

        plugin = await self.load_plugin(plugin_dir)
        if plugin:
            try:
                self.registry.register(plugin)
                logger.info(f"Reloaded plugin: {plugin_name}")
                return True
            except Exception as e:
                logger.error(f"Failed to register reloaded plugin {plugin_name}: {e}")
                return False

        return False

    def _unload_module(self, plugin_name: str) -> None:
        """Remove cached import state for a plugin."""
        package_name = self._module_packages.get(plugin_name, plugin_name)
        for module_name in (
            f"plugins.{package_name}.plugin",
            f"plugins.{package_name}",
        ):
            if module_name in sys.modules:
                del sys.modules[module_name]

        self._loaded_modules.pop(plugin_name, None)
        self._module_packages.pop(plugin_name, None)

    def get_plugin_info(self, plugin_name: str) -> Optional[Dict[str, Any]]:
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
