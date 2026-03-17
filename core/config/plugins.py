"""
Plugin-specific configuration settings.

Configuration for the plugin system.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Any

from pydantic import Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
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

    # Official Marketplace and Registry URLs
    REGISTRY_URL: str = Field(
        default="https://marketplace.baselithcore.xyz/api/marketplace/plugins/registry.json",
        validation_alias=AliasChoices(
            "MARKETPLACE_CENTRAL_URL", "PLUGIN_REGISTRY_URL", "REGISTRY_URL"
        ),
    )
    AUTH_URL: str = Field(
        default="https://marketplace.baselithcore.xyz",
        validation_alias=AliasChoices(
            "MARKETPLACE_AUTH_URL", "PLUGIN_AUTH_URL", "AUTH_URL"
        ),
    )

    registry_cache_ttl: int = Field(
        default=3600, description="TTL for local registry cache in seconds"
    )

    # Plugin-specific configs (loaded from config file or env)
    plugin_configs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict, description="Per-plugin configuration"
    )

    @property
    def registry_url(self) -> str:
        """Fixed official registry URL."""
        return self.REGISTRY_URL

    @property
    def auth_url(self) -> str:
        """Fixed official marketplace/auth URL."""
        return self.AUTH_URL


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
