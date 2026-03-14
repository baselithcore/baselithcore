"""Plugin loader for discovering and loading plugins from filesystem."""

import importlib.util
import sys
import types
from pathlib import Path
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv, dotenv_values
from core.observability.logging import get_logger

from .interface import Plugin
from .registry import PluginRegistry

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

    def discover_plugins(self) -> List[Path]:
        """
        Discover plugin directories.

        Returns:
            List of paths to plugin directories
        """
        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {self.plugins_dir}")
            return []

        plugin_dirs = []
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and not item.name.startswith((".", "_")):
                # Check if it has a plugin.py or __init__.py
                if (item / "plugin.py").exists() or (item / "__init__.py").exists():
                    plugin_dirs.append(item)
                    logger.debug(f"Discovered plugin directory: {item.name}")

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
        plugin_name = plugin_dir.name
        config = config or {}

        # Look for a plugin-specific .env file
        plugin_env = plugin_dir / ".env"
        if plugin_env.exists():
            # Extend global environment variables without overwriting main ones
            load_dotenv(plugin_env, override=False)
            logger.debug(f"Loaded plugin environment file: {plugin_env}")

            # Merge the plugin environment variables into the plugin config
            env_vars = dotenv_values(plugin_env)
            for k, v in env_vars.items():
                if k and v is not None:
                    # Prefer existing configs over .env defaults if already defined
                    # We merge strictly what's not in the config (case-insensitive keys)
                    k_lower = k.lower()
                    if k_lower not in [ck.lower() for ck in config.keys()]:
                        config[k_lower] = v

        # Track loading state if lifecycle manager available
        if self.lifecycle_manager:
            await self.lifecycle_manager.transition_to_loading(plugin_name)

        try:
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
            _ensure_parent_packages(plugin_name, plugin_dir)

            # For __init__.py the module *is* the package; for plugin.py
            # the module lives *inside* the package.
            if plugin_file.name == "__init__.py":
                module_fqn = f"plugins.{plugin_name}"
            else:
                module_fqn = f"plugins.{plugin_name}.plugin"

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
            module.__package__ = f"plugins.{plugin_name}"
            if plugin_file.name == "__init__.py":
                module.__path__ = [str(plugin_dir)]

            sys.modules[module_fqn] = module
            # Also register under the package name so lookups like
            # `import plugins.{name}` resolve to this module.
            sys.modules.setdefault(f"plugins.{plugin_name}", module)
            spec.loader.exec_module(module)

            self._loaded_modules[plugin_name] = module

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
        self, configs: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> int:
        """
        Discover and load all plugins with dependency resolution.

        Args:
            configs: Dictionary mapping plugin names to their configurations

        Returns:
            Number of successfully loaded plugins
        """
        configs = configs or {}
        plugin_dirs = self.discover_plugins()

        if not plugin_dirs:
            logger.info("No plugins found to load")
            return 0

        # Pass 1: Instantiate all plugins to read metadata (without initializing)
        instantiated_plugins: Dict[str, Plugin] = {}

        # If configs are provided, we only load plugins listed there
        filter_by_config = len(configs) > 0

        for plugin_dir in plugin_dirs:
            plugin_name = plugin_dir.name

            if filter_by_config:
                if plugin_name not in configs:
                    logger.debug(f"Skipping plugin {plugin_name} (not in config)")
                    continue

                # Check if enabled in config
                plugin_config = configs[plugin_name]
                if not plugin_config.get("enabled", True):
                    logger.info(f"Skipping disabled plugin {plugin_name}")
                    continue

            # Load without init
            plugin = await self.load_plugin(plugin_dir, initialize=False)
            if plugin:
                instantiated_plugins[plugin.metadata.name] = plugin

        if not instantiated_plugins:
            return 0

        # Pass 2: Sort by dependencies
        try:
            sorted_names = self._sort_by_dependencies(instantiated_plugins)
        except Exception as e:
            logger.error(f"Dependency resolution failed: {e}")
            return 0

        # Pass 3: Initialize and register in order
        loaded_count = 0
        for name in sorted_names:
            plugin = instantiated_plugins.get(name)
            if not plugin:
                continue

            try:
                # Initialize
                config = configs.get(name, {})
                await plugin.initialize(config)

                # Register
                self.registry.register(plugin)
                loaded_count += 1
                logger.info(f"Initialized and registered plugin: {name}")

            except Exception as e:
                logger.error(f"Failed to initialize/register plugin {name}: {e}")

        logger.info(f"Loaded {loaded_count}/{len(plugin_dirs)} plugins")
        return loaded_count

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
            module_name = f"plugins.{plugin_name}"
            if module_name in sys.modules:
                del sys.modules[module_name]
            del self._loaded_modules[plugin_name]

        # Reload
        plugin_dir = self.plugins_dir / plugin_name
        if not plugin_dir.exists():
            logger.error(f"Plugin directory not found: {plugin_dir}")
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
