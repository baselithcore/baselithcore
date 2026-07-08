"""DORA Register of Information persistence configuration.

Opt-in durable persistence for the register (:mod:`core.thirdparty.register`).
Default-off: when ``THIRDPARTY_REGISTER_DB_PATH`` is unset the register keeps
its non-durable in-memory store, so behaviour is byte-for-byte unchanged. When
a filesystem path is set, the singleton register swaps in a durable SQLite store
at that path so the register survives process restarts (DORA Art. 28(3) requires
it to be kept up to date).
"""

import logging

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ThirdPartyRegisterConfig(BaseSettings):
    """Configuration for the DORA Register of Information subsystem."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    register_db_path: str | None = Field(
        default=None, alias="THIRDPARTY_REGISTER_DB_PATH"
    )


_register_config: ThirdPartyRegisterConfig | None = None


def get_register_config() -> ThirdPartyRegisterConfig:
    """Get or create the global register-persistence configuration instance."""
    global _register_config
    if _register_config is None:
        _register_config = ThirdPartyRegisterConfig()
    return _register_config
