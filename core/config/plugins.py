"""
Plugin-specific configuration settings.

Configuration for the plugin system.
"""

import logging
from pathlib import Path
from typing import Any, Self

from pydantic import (
    AliasChoices,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
logger = logging.getLogger(__name__)


def installed_plugins_dir() -> Path:
    """Directory of the ``plugins`` package shipped beside ``core``.

    ``core`` and ``plugins`` ship in the same distribution, so the bundled
    plugins sit next to ``core/`` both in a checkout and in ``site-packages``.
    Resolved from this file, never from the cwd.
    """
    return Path(__file__).resolve().parents[2] / "plugins"


def resolve_plugins_root(path: Path | str | None = None) -> Path:
    """Resolve a plugin root the same way for every reader.

    The one resolver behind ``PLUGIN_PLUGINS_PATH``: the loader, the
    middleware pre-discovery, the plugin-update service and the CLI all read
    the directory this returns, so none of them depends on the cwd alone.

    Args:
        path: The configured root; ``None`` means the default ``plugins``.

    Returns:
        An absolute path unchanged. A relative path resolves against the cwd
        when that directory exists (a checkout, or a project with its own
        ``plugins/``); otherwise it falls back to the installed ``plugins``
        package, so an app started from any directory still finds the bundled
        plugins. When neither exists the cwd-relative path is returned, so a
        marketplace install can create it.
    """
    candidate = Path(path) if path is not None else Path("plugins")
    if candidate.is_absolute():
        return candidate
    in_cwd = Path.cwd() / candidate
    if in_cwd.is_dir():
        return in_cwd
    installed = installed_plugins_dir()
    if installed.is_dir():
        return installed
    return in_cwd


def plugin_install_root(config: "PluginConfig | None" = None) -> Path:
    """Where marketplace and CLI installs write plugins.

    Never the installed ``plugins`` package: :func:`resolve_plugins_root`
    falls back to it so an app started anywhere still *reads* the bundled
    plugins, but a write there lands in ``site-packages`` — lost on the next
    upgrade and needing write access to the interpreter.

    Args:
        config: The plugin configuration; the global one when ``None``.

    Returns:
        ``PLUGIN_PLUGINS_PATH`` when set (a relative value taken against the
        cwd, whether or not it exists yet), else ``./plugins`` under the cwd.
        The directory may not exist; the installer creates it. Discovery
        scans it beside the bundled package (see
        :func:`core.plugins.discovery.with_bundled_plugins`).
    """
    cfg = config if config is not None else get_plugin_config()
    candidate = cfg.configured_plugins_path or Path("plugins")
    return candidate if candidate.is_absolute() else Path.cwd() / candidate


class PluginConfig(BaseSettings):
    """
    Plugin system configuration.

    Environment variables: PLUGIN_ENABLED, PLUGIN_AUTO_LOAD, etc.
    """

    model_config = SettingsConfigDict(
        env_prefix="PLUGIN_",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = Field(default=True, description="Enable plugin system")

    auto_load: bool = Field(
        default=True, description="Automatically load plugins on startup"
    )

    plugins_path: Path = Field(
        default=Path("plugins"),
        validate_default=True,
        description="Plugin root: where marketplace installs write and what the "
        "runtime loaders scan",
    )

    config_path: Path | None = Field(
        default=None, description="Path to plugin configuration file"
    )

    # Official Marketplace and Registry URLs
    # This is the hardcoded "Source of Truth" for the official marketplace.
    OFFICIAL_MARKETPLACE_URL: str = "https://marketplace.baselithcore.xyz"

    REGISTRY_URL: str = Field(
        default="https://marketplace.baselithcore.xyz/api/marketplace/plugins/registry.json",
        validation_alias=AliasChoices(
            "MARKETPLACE_CENTRAL_URL", "PLUGIN_REGISTRY_URL", "REGISTRY_URL"
        ),
        description="URL for discovering and downloading plugins (can be overriden for local mirrors)",
    )
    AUTH_URL: str = Field(
        default="https://marketplace.baselithcore.xyz",
        validation_alias=AliasChoices(
            "MARKETPLACE_AUTH_URL", "PLUGIN_AUTH_URL", "AUTH_URL"
        ),
        description="URL for the official marketplace authentication portal",
    )

    registry_cache_ttl: int = Field(
        default=3600, description="TTL for local registry cache in seconds"
    )

    publish_workspace_root: Path | None = Field(
        default=None,
        description=(
            "POST /api/backstage/publish only packages plugin directories "
            "inside this root (e.g. the Backstage Scaffolder workspace mount). "
            "Fail-closed: while unset the publish endpoint is disabled, so a "
            "job/admin caller can never point the publisher at an arbitrary "
            "host directory."
        ),
    )

    # Deprecated, no effect: per-plugin configuration is read from the plugin
    # config file (core.plugins.config_file), never from this field.
    plugin_configs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Deprecated, no effect: nothing reads it; per-plugin configuration "
            "lives in configs/plugins.yaml (or the file PLUGIN_CONFIG_PATH "
            "names)"
        ),
    )

    #: ``plugins_path`` as configured, before resolution; ``None`` when unset.
    _configured_plugins_path: Path | None = PrivateAttr(default=None)

    @field_validator("plugins_path")
    @classmethod
    def _resolve_plugins_path(cls, value: Path) -> Path:
        """Resolve the root once, through :func:`resolve_plugins_root`."""
        return resolve_plugins_root(value)

    @model_validator(mode="wrap")
    @classmethod
    def _remember_configured_root(
        cls, data: Any, handler: ModelWrapValidatorHandler[Self]
    ) -> Self:
        """Keep the raw ``plugins_path`` for :func:`plugin_install_root`.

        Resolution may swap a relative root for the installed package — right
        for reading, never a place to write — so the configured value has to
        survive it.
        """
        raw = data.get("plugins_path") if isinstance(data, dict) else None
        instance = handler(data)
        instance._configured_plugins_path = Path(raw) if raw is not None else None
        return instance

    @property
    def configured_plugins_path(self) -> Path | None:
        """``PLUGIN_PLUGINS_PATH`` as given, unresolved; ``None`` when unset."""
        return self._configured_plugins_path

    @property
    def registry_url(self) -> str:
        """Fixed official registry URL."""
        return self.REGISTRY_URL

    @property
    def auth_url(self) -> str:
        """Fixed official marketplace/auth URL."""
        return self.AUTH_URL


# Global instance
_plugin_config: PluginConfig | None = None


def get_plugin_config() -> PluginConfig:
    """Get or create the global plugin configuration instance."""
    global _plugin_config
    if _plugin_config is None:
        _plugin_config = PluginConfig()
        logger.debug(
            "Initialized PluginConfig with enabled=%s, auto_load=%s, plugins_path=%s",
            _plugin_config.enabled,
            _plugin_config.auto_load,
            _plugin_config.plugins_path,
        )
    return _plugin_config
