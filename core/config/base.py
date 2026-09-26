"""
Core configuration settings for the BaselithCore framework.

This module defines the central `CoreConfig` class using Pydantic Settings,
providing a structured way to handle framework-wide settings with environment
variable overrides and default values.
"""

import logging
from pathlib import Path

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
        description="Deprecated, no effect: nothing reads it; JSON logs are selected by LOG_JSON",
    )

    log_structured: bool = Field(
        default=False,
        description="Deprecated, no effect: nothing reads it; JSON logs are selected by LOG_JSON",
    )

    # Directories
    plugin_dir: Path = Field(
        default=Path("plugins"),
        description=(
            "Deprecated, no effect: the plugin loader reads PLUGIN_PLUGINS_PATH"
        ),
    )

    data_dir: Path = Field(
        default=Path("data"), description="Directory for data storage"
    )

    documents_dir: Path = Field(
        default=Path("documents"),
        description=(
            "Deprecated, no effect: the framework never reads it; document "
            "sources configure their own paths"
        ),
    )

    # Application
    app_name: str = Field(default="Baselith-Core", description="Application name")

    debug: bool = Field(default=False, description="Enable debug mode")

    # Performance and Concurrency
    max_workers: int = Field(
        default=4,
        description=(
            "Deprecated, no effect: nothing reads it; the inference thread pool "
            "is sized by BASELITH_INFERENCE_THREADS and the per-worker math "
            "thread pools by OMP_NUM_THREADS (split across web workers "
            "automatically)"
        ),
    )

    # Framework Execution Mode
    deterministic_mode: bool = Field(
        default=False,
        description=(
            "When enabled, seeds Python's random (and numpy) at startup and pins LLM "
            "sampling on every generation path (temperature 0, plus seed and top_p=1 "
            "where the provider supports them). Does not disable caches or hash "
            "randomization; set PYTHONHASHSEED before launching the process."
        ),
    )

    random_seed: int = Field(
        default=42, description="Random seed when deterministic_mode is enabled"
    )


# Global instance
_core_config: CoreConfig | None = None


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
