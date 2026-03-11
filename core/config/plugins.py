"""
Plugin-specific configuration settings.

Configuration for the plugin system.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class PluginConfig(BaseSettings):
    """
    Plugin system configuration.

    Environment variables: PLUGIN_ENABLED, PLUGIN_AUTO_LOAD, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="PLUGIN_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=True, description="Enable plugin system")

    auto_load: bool = Field(
        default=True, description="Automatically load plugins on startup"
    )

    plugins_path: Path = Field(
        default=Path("plugins"), description="Directory where plugins are installed"
    )

    config_path: Optional[Path] = Field(
        default=None, description="Path to plugin configuration file"
    )

    registry_url: str = Field(
        default="https://raw.githubusercontent.com/baselith/marketplace/main/registry.json",
        description="URL of the remote plugin registry",
    )

    registry_cache_ttl: int = Field(
        default=3600, description="TTL for local registry cache in seconds"
    )

    # Plugin-specific configs (loaded from config file or env)
    plugin_configs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict, description="Per-plugin configuration"
    )


# Global instance
_plugin_config: Optional[PluginConfig] = None


def get_plugin_config() -> PluginConfig:
    """Get or create the global plugin configuration instance."""
    global _plugin_config
    if _plugin_config is None:
        _plugin_config = PluginConfig()
        logger.info(
            f"Initialized PluginConfig with enabled={_plugin_config.enabled}, auto_load={_plugin_config.auto_load}"
        )
    return _plugin_config
