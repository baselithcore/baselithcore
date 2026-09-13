"""Whole-tree plugin load: discovery, dependency ordering, admission gates.

The three-pass load (instantiate → topologically sort → initialize/register)
lives here rather than on :class:`~core.plugins.loader.PluginLoader` so that
module stays under the 500-line cap. ``PluginLoader.load_all_plugins`` is a thin
delegator, and the public behaviour is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger
from core.utils.logsafe import sanitize_log_value

from .integrity import enforce_signing_policy
from .interface import Plugin
from .load_gates import compat_gate, config_gate

if TYPE_CHECKING:
    from .loader import PluginLoader

logger = get_logger(__name__)


async def _instantiate_all(
    loader: PluginLoader,
    configs: dict[str, dict[str, Any]],
    plugin_dirs: list[Any],
) -> tuple[dict[str, Plugin], dict[str, dict[str, Any]]]:
    """Pass 1 — import and instantiate every eligible plugin, without init.

    Returns:
        The instantiated plugins by manifest name, and the config each should
        be initialized with.
    """
    instantiated: dict[str, Plugin] = {}
    configs_by_name: dict[str, dict[str, Any]] = {}

    # If configs are provided, we only load plugins listed there.
    filter_by_config = len(configs) > 0

    for plugin_dir in plugin_dirs:
        # One broken plugin must never abort the tree. Everything in the body
        # can raise on hostile input — ``discover_plugin`` reads a manifest,
        # and ``plugin.metadata`` re-parses it and now raises
        # ManifestValidationError on an unknown key — and an exception escaping
        # here would take down every *other* plugin's load with it.
        try:
            await _instantiate_one(
                loader,
                plugin_dir,
                configs,
                filter_by_config=filter_by_config,
                instantiated=instantiated,
                configs_by_name=configs_by_name,
            )
        except Exception as exc:
            logger.error(
                "Skipping plugin %s: %s",
                sanitize_log_value(plugin_dir.name),
                exc,
                exc_info=True,
            )

    return instantiated, configs_by_name


async def _instantiate_one(
    loader: PluginLoader,
    plugin_dir: Any,
    configs: dict[str, dict[str, Any]],
    *,
    filter_by_config: bool,
    instantiated: dict[str, Plugin],
    configs_by_name: dict[str, dict[str, Any]],
) -> None:
    """Import and instantiate one plugin, recording it when it loads.

    Raises:
        Exception: Any failure reading the plugin's manifest or importing it.
            The caller turns that into "skip this plugin" — see
            :func:`_instantiate_all`.
    """
    discovery = loader.resource_analyzer.discover_plugin(plugin_dir)
    plugin_name = discovery.name if discovery else plugin_dir.name
    safe_name = sanitize_log_value(plugin_name)
    config_key = None

    if filter_by_config:
        config_key = loader.match_config_key(configs, plugin_dir.name, plugin_name)
        if config_key is None:
            logger.debug(f"Skipping plugin {safe_name} (not in config)")
            return

        plugin_config = configs[config_key]
        if not plugin_config.get("enabled", True):
            logger.info(f"Skipping disabled plugin {safe_name}")
            return

    plugin = await loader.load_plugin(plugin_dir, initialize=False)
    if plugin:
        instantiated[plugin.metadata.name] = plugin
        configs_by_name[plugin.metadata.name] = configs.get(
            config_key or plugin.metadata.name, {}
        )


async def load_all_plugins(
    loader: PluginLoader,
    configs: dict[str, dict[str, Any]] | None = None,
    *,
    activate_on_load: bool = True,
) -> int:
    """Discover and load all plugins with dependency resolution.

    Args:
        loader: The loader whose registry and plugins directory to use.
        configs: Dictionary mapping plugin names to their configurations.
        activate_on_load: Initialize each plugin immediately; otherwise register
            it cold for lazy activation.

    Returns:
        Number of successfully loaded plugins.
    """
    configs = configs or {}
    # Surface (and optionally hard-fail on) an insecure signing posture
    # before any plugin code is loaded.
    enforce_signing_policy()
    plugin_dirs = loader.discover_plugins()

    if not plugin_dirs:
        logger.info("No plugins found to load")
        return 0

    instantiated_plugins, plugin_configs_by_name = await _instantiate_all(
        loader, configs, plugin_dirs
    )
    if not instantiated_plugins:
        return 0

    # Map of available plugin name -> version for dependency compat checks.
    available_versions = {
        name: plugin.metadata.version for name, plugin in instantiated_plugins.items()
    }

    # Pass 2: Sort by dependencies
    try:
        sorted_names = loader.sort_by_dependencies(instantiated_plugins)
    except Exception as e:
        logger.error(f"Dependency resolution failed: {e}")
        return 0

    # Pass 3: Initialize immediately or register for lazy activation
    loaded_count = 0
    for name in sorted_names:
        plugin = instantiated_plugins.get(name)
        if not plugin:
            continue
        safe_name = sanitize_log_value(name)

        try:
            config = plugin_configs_by_name.get(name, {})

            # Gate on version compatibility and config schema. Both fail closed:
            # an unsatisfied declaration skips the plugin unless the matching
            # BASELITH_ENFORCE_PLUGIN_{COMPAT,CONFIG}=false downgrade is set.
            if not compat_gate(plugin, available_versions):
                continue
            if not config_gate(plugin, config):
                continue

            if activate_on_load:
                await plugin.initialize(config)
                loader.registry.register(plugin)
                logger.info(f"Initialized and registered plugin: {safe_name}")
            else:
                loader.registry.register(plugin, require_initialized=False)
                logger.info(f"Registered plugin for lazy activation: {safe_name}")
            loaded_count += 1

        except Exception as e:
            logger.error(f"Failed to initialize/register plugin {safe_name}: {e}")

    logger.info(f"Loaded {loaded_count}/{len(plugin_dirs)} plugins")
    return loaded_count


__all__ = ["load_all_plugins"]
