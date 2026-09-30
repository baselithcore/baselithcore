"""Configuration for signed plugin updates (release polling and overlay)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: The public core project, whose releases and advisories the notice reads.
PUBLIC_CORE_REPO = "baselithcore/baselithcore"
#: Environment names that once pointed the notice at another repository. The
#: notice now always references the public core release, so they are ignored.
LEGACY_REPO_ENV = ("SYSTEM_UPDATE_REPO", "PLUGIN_UPDATE_SYSTEM_UPDATE_REPO")
_legacy_warned = False


def warn_ignored_legacy_env(environ: Mapping[str, str] | None = None) -> bool:
    """Log, once per process, that a legacy repo variable is set and ignored.

    Args:
        environ: The environment to inspect (``os.environ`` when omitted).

    Returns:
        True when this call logged the warning.
    """
    global _legacy_warned
    env = os.environ if environ is None else environ
    names = [name for name in LEGACY_REPO_ENV if env.get(name, "").strip()]
    if not names or _legacy_warned:
        return False
    _legacy_warned = True
    logger.warning(
        "%s is set but ignored: the system update notice always compares the "
        "running core with the public core release (CORE_UPDATE_REPO, default "
        "%s). Remove the variable from this deployment.",
        ", ".join(names),
        PUBLIC_CORE_REPO,
    )
    return True


def _https_link(value: str | None) -> str | None:
    """``value`` when it is an absolute https URL without credentials, else None."""
    text = (value or "").strip()
    if not text:
        return None
    parts = urlsplit(text)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or any(ch.isspace() for ch in text)
    ):
        logger.warning(
            "SYSTEM_UPGRADE_GUIDE_URL ignored: it must be an absolute https URL"
        )
        return None
    return text


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

    core_update_repo: str = Field(
        # Literal, not PUBLIC_CORE_REPO: the generated configuration reference
        # renders the default from the source.
        default="baselithcore/baselithcore",
        validation_alias=AliasChoices(
            "CORE_UPDATE_REPO", "PLUGIN_UPDATE_CORE_UPDATE_REPO"
        ),
        description="GitHub owner/repo of the public core project, whose releases "
        "and security advisories are compared with the running core release "
        "(core/_core_version.py; env CORE_UPDATE_REPO); empty disables the "
        "system update notice",
    )
    upgrade_guide_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SYSTEM_UPGRADE_GUIDE_URL", "PLUGIN_UPDATE_UPGRADE_GUIDE_URL"
        ),
        description="https link to this deployment's upgrade instructions, shown "
        "with the system update notice (env SYSTEM_UPGRADE_GUIDE_URL); anything "
        "but an absolute https URL is ignored",
    )

    instance_id: str = Field(
        default="",
        description="Identity of this deployment for update announcements; "
        "deployments sharing one Redis or cache directory use distinct values so "
        "they do not suppress each other's notices (falls back to the "
        "APP_BASE_URL host; with neither set the Redis key is shared and a "
        "warning is logged)",
    )

    @field_validator("upgrade_guide_url", mode="before")
    @classmethod
    def _guide_is_https(cls, value: object) -> str | None:
        """A notice link must never become a phishing or script vector."""
        return _https_link(value if isinstance(value, str) else None)

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
        """True when a core repo is configured."""
        return bool(self.core_update_repo.strip())

    @property
    def enabled(self) -> bool:
        """True when either the plugin check or the system check can run."""
        return self.plugin_checks_enabled or self.system_checks_enabled


@lru_cache(maxsize=1)
def get_plugin_update_config() -> PluginUpdateConfig:
    """Get the cached plugin update configuration.

    Also warns (once) when a legacy repo variable is still set: it no longer
    changes anything, and an operator should know that.
    """
    warn_ignored_legacy_env()
    return PluginUpdateConfig()


__all__ = [
    "LEGACY_REPO_ENV",
    "PUBLIC_CORE_REPO",
    "PluginUpdateConfig",
    "get_plugin_update_config",
    "warn_ignored_legacy_env",
]
