"""One-click plugin update (host installs): the updater's and the console's settings.

Off by default (``UPDATE_APPLY_ENABLED=false``). Read by the web process (to
decide whether a candidate is installable) and by ``baselith plugin-updater
serve`` (to execute). Environment prefix ``UPDATE_APPLY_``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from core.config._collections import csv_list


class UpdateApplyConfig(BaseSettings):
    """Settings of the plugin updater (``UPDATE_APPLY_*``)."""

    model_config = SettingsConfigDict(
        env_prefix="UPDATE_APPLY_", case_sensitive=False, extra="ignore"
    )

    enabled: bool = Field(
        default=False, description="Kill switch: one-click plugin updates on this host"
    )
    state_dir: Path = Field(
        default=Path("data/plugin_updates/apply"),
        description="Run store shared by the API and the updater",
    )
    restart_command: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="argv (JSON list or comma-separated) that restarts the API service; no shell",
    )
    restart_timeout_seconds: int = Field(
        default=60, ge=5, le=600, description="Timeout of the restart command"
    )
    health_url: str = Field(
        default="http://127.0.0.1:8000/health/ready",
        description="Readiness URL probed after a restart",
    )
    health_timeout_seconds: int = Field(
        default=180,
        ge=30,
        le=1800,
        description="Deadline for the post-restart health check",
    )
    stable_seconds: int = Field(
        default=20,
        ge=5,
        le=300,
        description="Readiness must hold this long without a failure",
    )
    keep_versions: int = Field(
        default=2, ge=2, le=10, description="Store entries kept per plugin"
    )
    schema_init: bool = Field(
        default=True,
        description="Run `baselith plugin schema-init --plugin <name>` before the restart",
    )
    schema_env_file: Path | None = Field(
        default=None,
        description="dotenv with the schema owner's DB credentials, for schema-init only",
    )
    heartbeat_seconds: int = Field(
        default=5, ge=1, le=60, description="Updater heartbeat period"
    )
    poll_seconds: float = Field(
        default=2.0,
        ge=0.2,
        le=30.0,
        description="Updater poll period for approved runs",
    )
    approval_ttl_seconds: int = Field(
        default=24 * 3600,
        ge=60,
        le=30 * 24 * 3600,
        description="Expiry of approval requests created without their own window",
    )

    @model_validator(mode="after")
    def _absolute_state_dir(self) -> UpdateApplyConfig:
        # The API and the updater are separate processes that may not share a
        # cwd, so the store must be an absolute path. A relative value (the
        # default included) is resolved against the cwd at load; with the switch
        # on in production it is refused instead, so nothing guesses a location.
        if not self.state_dir.is_absolute():
            if self.enabled and os.environ.get("APP_ENV", "").lower() == "production":
                raise ValueError(
                    "state_dir must be absolute when UPDATE_APPLY_ENABLED and APP_ENV=production"
                )
            self.state_dir = self.state_dir.resolve()
        return self

    @field_validator("restart_command", mode="before")
    @classmethod
    def _parse_argv(cls, value: Any) -> Any:
        return csv_list(value)

    @field_validator("restart_command")
    @classmethod
    def _argv(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item for item in value):
            raise ValueError(
                "restart_command items must be non-empty and contain no NUL"
            )
        return value

    @field_validator("health_url")
    @classmethod
    def _http(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("health_url must be an http(s) URL with a host")
        return value

    @property
    def restart_configured(self) -> bool:
        """True when a restart command is set."""
        return bool(self.restart_command)


@lru_cache(maxsize=1)
def get_update_apply_config() -> UpdateApplyConfig:
    """The cached settings (the updater re-reads nothing else across runs)."""
    return UpdateApplyConfig()


__all__ = ["UpdateApplyConfig", "get_update_apply_config"]
