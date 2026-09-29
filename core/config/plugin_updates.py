"""Configuration for signed plugin updates (release polling and overlay)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class PluginUpdateConfig(BaseSettings):
    """Plugin update checker settings.

    Environment variables: ``PLUGIN_UPDATE_SOURCES_FILE``,
    ``PLUGIN_UPDATE_GITHUB_TOKEN``, ``PLUGIN_UPDATE_CHECK_INTERVAL_SECONDS``...
    """

    model_config = SettingsConfigDict(
        env_prefix="PLUGIN_UPDATE_",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    sources_file: Path | None = Field(
        default=None,
        description="YAML file mapping plugin names to their GitHub mirror repos "
        "(the mirror registry); update checks are off while unset",
    )
    github_token: SecretStr | None = Field(
        default=None,
        description="GitHub token with read access to the mirror repos' releases",
    )
    github_api_url: str = Field(
        default="https://api.github.com",
        description="GitHub API base URL (override for GitHub Enterprise); https "
        "only, plain http is accepted for a loopback host (a local fake)",
    )
    check_interval_seconds: int = Field(
        default=21600,
        ge=300,
        description="Seconds between automatic update checks",
    )
    max_artifact_mb: int = Field(
        default=200,
        ge=1,
        description="Largest release artifact downloaded, in MB; a larger one is "
        "refused before its signature is checked (it unpacks to at most 4x this)",
    )
    cache_dir: Path = Field(
        default=Path("data/plugin_updates"),
        description="Where downloaded release artifacts and the last check are cached",
    )

    system_update_repo: str = Field(
        default="baselithcore/baselithcore",
        validation_alias=AliasChoices(
            "SYSTEM_UPDATE_REPO", "PLUGIN_UPDATE_SYSTEM_UPDATE_REPO"
        ),
        description="GitHub owner/repo whose releases and security advisories are "
        "compared with the running framework version (env SYSTEM_UPDATE_REPO); "
        "empty disables the system update notice",
    )

    @field_validator("github_api_url")
    @classmethod
    def _api_url_is_https(cls, value: str) -> str:
        """The token travels to this URL: https, or http only on loopback."""
        parts = urlsplit(value)
        if not parts.hostname:
            raise ValueError("github_api_url must be an absolute URL")
        if parts.scheme == "https":
            return value
        if parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS:
            return value
        raise ValueError(
            "github_api_url must use https (http is allowed only for "
            "127.0.0.1, ::1 or localhost)"
        )

    @property
    def plugin_checks_enabled(self) -> bool:
        """True when a sources file is configured and exists."""
        return self.sources_file is not None and self.sources_file.is_file()

    @property
    def system_checks_enabled(self) -> bool:
        """True when a system repo is configured."""
        return bool(self.system_update_repo.strip())

    @property
    def enabled(self) -> bool:
        """True when either the plugin check or the system check can run."""
        return self.plugin_checks_enabled or self.system_checks_enabled


@lru_cache(maxsize=1)
def get_plugin_update_config() -> PluginUpdateConfig:
    """Get the cached plugin update configuration."""
    return PluginUpdateConfig()


__all__ = ["PluginUpdateConfig", "get_plugin_update_config"]
