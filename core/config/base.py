"""
Core configuration settings for the BaselithCore framework.

This module defines the central `CoreConfig` class using Pydantic Settings,
providing a structured way to handle framework-wide settings with environment
variable overrides and default values.
"""

import logging
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# NOTE: Using direct logging.getLogger() here instead of core.observability.logging.get_logger()
# This is intentional: config modules initialize during framework bootstrap, before the
# observability infrastructure is fully set up. Direct logging prevents circular dependencies.
logger = logging.getLogger(__name__)


class CoreConfig(BaseSettings):
    """
    Core framework configuration.

    All settings can be overridden via environment variables with CORE_ prefix.
    """

    model_config = SettingsConfigDict(
        # All environment variables must start with CORE_ (e.g., CORE_LOG_LEVEL)
        env_prefix="CORE_",
        # Load settings from .env file if it exists
        env_file=".env",
        env_file_encoding="utf-8",
        # Case-insensitive matching for environment variables
        case_sensitive=False,
        # Allow extra fields in environment without failing
        extra="ignore",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    log_format: str = Field(
        default="text",
        description="Logging format (text or json)",
    )

    log_structured: bool = Field(
        default=False,
        description="Enable structured logging (JSON format)",
    )

    # Directories
    plugin_dir: Path = Field(
        default=Path("plugins"), description="Directory containing plugins"
    )

    data_dir: Path = Field(
        default=Path("data"), description="Directory for data storage"
    )

    documents_dir: Path = Field(
        default=Path("documents"), description="Directory for document storage"
    )

    # Application
    app_name: str = Field(default="Baselith-Core", description="Application name")

    debug: bool = Field(default=False, description="Enable debug mode")

    # Performance and Concurrency
    max_workers: int = Field(
        default=4,
        description="Maximum number of worker threads for parallel orchestration and background tasks",
    )

    # Framework Execution Mode
    deterministic_mode: bool = Field(
        default=False,
        description=(
            "When enabled, ensures reproducible execution by pinning seeds and disabling non-deterministic "
            "features (e.g., setting LLM temperature to 0 and bypassing caches)."
        ),
    )

    random_seed: int = Field(
        default=42, description="Random seed when deterministic_mode is enabled"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure directories exist
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)

        # Configure logging - DISABLED to avoid side effects in backend.py
        # Logging should be configured explicitly by the entry point (cli, backend, etc)
        # from core.observability.logging import configure_logging
        #
        # configure_logging(
        #     level=self.log_level,
        #     json_output=self.log_structured or (self.log_format == "json"),
        # )


# Global instance
_core_config: Optional[CoreConfig] = None


def get_core_config() -> CoreConfig:
    """
    Retrieve the global singleton instance of CoreConfig.

    If the instance doesn't exist, it is initialized on the first call.
    Settings are automatically loaded from environment variables and .env files.

    Returns:
        CoreConfig: The global configuration instance.
    """
    global _core_config
    if _core_config is None:
        _core_config = CoreConfig()
        logger.info(f"Initialized CoreConfig with log_level={_core_config.log_level}")
    return _core_config
