"""Load-time admission gates for plugins.

These helpers decide whether a plugin may proceed to initialize based on its
declared version compatibility and config schema. Both gates are warn-only by
default and skip the plugin only when their matching enforcement flag is set
(``BASELITH_ENFORCE_PLUGIN_COMPAT`` / ``BASELITH_ENFORCE_PLUGIN_CONFIG``), so
existing deployments keep their current behavior. Kept out of ``loader.py`` to
respect the 500-line module cap.
"""

from __future__ import annotations

from typing import Any, Dict

from core._version import __version__ as CORE_VERSION
from core.observability.logging import get_logger

from .config_validation import is_config_enforcement_enabled, validate_plugin_config
from .interface import Plugin
from .version import check_plugin_compatibility, is_compat_enforcement_enabled

logger = get_logger(__name__)


def config_gate(plugin: Plugin, config: Dict[str, Any]) -> bool:
    """Validate a plugin's config against its declared JSON Schema.

    Returns True when the plugin may proceed to initialize. When validation
    fails, the plugin is skipped only if config enforcement is enabled;
    otherwise problems are logged as warnings and loading continues.
    """
    try:
        schema = plugin.get_config_schema()
    except Exception as e:  # defensive: a broken hook must not block loading
        logger.warning(f"Could not read config schema for {plugin.metadata.name}: {e}")
        return True

    problems = validate_plugin_config(schema, config)
    if not problems:
        return True

    detail = "; ".join(problems)
    if is_config_enforcement_enabled():
        logger.error(
            f"Skipping plugin {plugin.metadata.name}: invalid config ({detail})"
        )
        return False
    logger.warning(f"Plugin {plugin.metadata.name} config validation warning: {detail}")
    return True


def compat_gate(plugin: Plugin, available_versions: Dict[str, str]) -> bool:
    """Check a plugin's core/plugin-dependency version compatibility.

    Returns True when the plugin may load. Incompatibilities skip the plugin
    only if compat enforcement is enabled; otherwise they are logged as warnings
    and loading continues (preserving legacy behavior).
    """
    md = plugin.metadata
    problems = check_plugin_compatibility(
        core_version=CORE_VERSION,
        min_core_version=md.min_core_version,
        max_core_version=md.max_core_version,
        plugin_dependencies=md.plugin_dependencies,
        available_versions=available_versions,
    )
    if not problems:
        return True

    detail = "; ".join(problems)
    if is_compat_enforcement_enabled():
        logger.error(f"Skipping incompatible plugin {md.name}: {detail}")
        return False
    logger.warning(f"Plugin {md.name} compatibility warning: {detail}")
    return True
