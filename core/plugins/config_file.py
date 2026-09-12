"""The plugin configuration file, read the same way by every loader.

``configs/plugins.yaml`` (or the file ``PLUGIN_CONFIG_PATH`` points at) decides
which plugins a process runs. Three code paths consult it — the lifespan's
lazy discovery, the async loader and the synchronous app-middleware
pre-discovery that runs inside ``create_app()`` — and they must agree, or a
plugin disabled in the file still gets its ``setup_app_middleware`` hook (an
SPA mount, an ASGI middleware) installed at construction time while its
routers are correctly absent. That is the split this module closes: one
reader, one containment rule, one enable-list rule.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from core.plugins._ast_utils import match_config_key

logger = logging.getLogger(__name__)

DEFAULT_PLUGIN_CONFIG_PATH = "configs/plugins.yaml"
PLUGIN_CONFIG_PATH_ENV = "PLUGIN_CONFIG_PATH"

PluginConfigs = dict[str, dict[str, Any]]


def resolve_plugin_config_path(cwd: Path | None = None) -> Path:
    """Return the config file path, refusing one outside the working directory.

    Args:
        cwd: The working directory the path must resolve under; defaults to
            the process's.

    Raises:
        ValueError: The path escapes ``cwd`` — a ``PLUGIN_CONFIG_PATH`` that
            points elsewhere is an operator error, not a file to read.
    """
    base = (cwd or Path.cwd()).resolve()
    raw = os.environ.get(PLUGIN_CONFIG_PATH_ENV, DEFAULT_PLUGIN_CONFIG_PATH)
    path = (
        (base / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    )
    if not path.is_relative_to(base):
        raise ValueError(
            f"{PLUGIN_CONFIG_PATH_ENV} must resolve inside {base}; got {path}"
        )
    return path


def read_plugin_configs(cwd: Path | None = None) -> PluginConfigs:
    """Read the plugin config file into ``{plugin: block}``.

    Never raises: a missing file is an empty configuration (every discovered
    plugin runs), an unreadable or malformed one is logged and treated the
    same way, so ``create_app()`` and the lifespan degrade identically.
    """
    try:
        path = resolve_plugin_config_path(cwd)
    except ValueError as exc:
        logger.error("❌ Failed to resolve plugin configuration path: %s", exc)
        return {}
    if not path.exists():
        logger.warning("⚠️ Plugin configuration file not found: %s", path)
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
    except Exception as exc:
        logger.error("❌ Failed to load plugin configurations from %s: %s", path, exc)
        return {}
    if not isinstance(loaded, dict):
        logger.error(
            "❌ Plugin configuration at %s is not a mapping; ignoring it", path
        )
        return {}
    logger.info("📄 Loaded plugin configurations from %s", path)
    return {str(k): (v if isinstance(v, dict) else {}) for k, v in loaded.items()}


def plugin_enabled(
    configs: PluginConfigs, directory_name: str, plugin_name: str
) -> bool:
    """Apply the enable-list rule every loader shares.

    An empty configuration enables everything. A non-empty one enables only
    the plugins it names (by directory name, manifest name or their
    ``-``/``_`` variants), and only when the block does not say
    ``enabled: false``.
    """
    if not configs:
        return True
    key = match_config_key(configs, directory_name, plugin_name)
    if key is None:
        return False
    return bool(configs[key].get("enabled", True))


__all__ = [
    "DEFAULT_PLUGIN_CONFIG_PATH",
    "PLUGIN_CONFIG_PATH_ENV",
    "PluginConfigs",
    "plugin_enabled",
    "read_plugin_configs",
    "resolve_plugin_config_path",
]
