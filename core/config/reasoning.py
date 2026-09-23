"""Reasoning configuration (``TOT_``).

Tree-of-Thoughts search shape: branching, depth and the scoring policy that
decides which branches survive.
"""

import logging
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class ReasoningConfig(BaseSettings):
    """
    Advanced Reasoning (ToT) configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="TOT_",
        case_sensitive=False,
        extra="ignore",
    )

    max_depth: int = Field(
        default=3,
        ge=1,
        description="Default ToT depth (max_steps) for the reasoning handler",
    )
    branching_factor: int = Field(
        default=3,
        ge=1,
        description="Default ToT branching factor (k) for the reasoning handler",
    )
    beam_width: int = Field(
        default=3,
        description="Deprecated and ignored: the ToT engine has no beam search",
    )
    strategy: Literal["bfs", "mcts"] = Field(
        default="bfs",
        description="Default ToT search strategy for the reasoning handler: "
        "'bfs' (bounded best-first expansion) or 'mcts'. The retired 'dfs' "
        "value, never implemented, is read as 'bfs'.",
    )

    @field_validator("strategy", mode="before")
    @classmethod
    def _retire_dfs(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() == "dfs":
            logger.warning("TOT_STRATEGY=dfs was never implemented; using 'bfs'")
            return "bfs"
        return value

    # Self-correction settings
    self_correction_max_iterations: int = Field(
        default=2, description="Maximum self-correction iterations"
    )

    # ThoughtCache settings
    thought_cache_maxsize: int = Field(
        default=1000, description="Maximum entries in thought cache"
    )
    thought_cache_ttl: float = Field(
        default=1800.0, description="Thought cache TTL in seconds (30 min)"
    )


# Global instance
_reasoning_config: ReasoningConfig | None = None


def get_reasoning_config() -> ReasoningConfig:
    """Get or create the global reasoning configuration instance."""
    global _reasoning_config
    if _reasoning_config is None:
        _reasoning_config = ReasoningConfig()
    return _reasoning_config
