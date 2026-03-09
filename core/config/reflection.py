"""
Reflection Pattern Configuration.

Provides centralized configuration for the Reflection agentic design pattern.
"""

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class ReflectionConfig(BaseSettings):
    """Configuration for the Reflection pattern."""

    model_config = SettingsConfigDict(env_prefix="REFLECTION_")

    max_iterations: int = 3
    """Maximum number of refinement iterations."""

    quality_threshold: float = 0.7
    """Minimum acceptable quality score (0.0 to 1.0)."""


# Global instance
_reflection_config: Optional[ReflectionConfig] = None


def get_reflection_config() -> ReflectionConfig:
    """Get or create global Reflection config."""
    global _reflection_config
    if _reflection_config is None:
        _reflection_config = ReflectionConfig()
    return _reflection_config
