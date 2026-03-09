from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional, Literal


class ReasoningConfig(BaseSettings):
    """
    Advanced Reasoning (ToT) configuration.
    """

    model_config = SettingsConfigDict(
        env_prefix="TOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    max_depth: int = Field(default=3, description="Maximum search depth")
    branching_factor: int = Field(default=3, description="Branching factor per node")
    beam_width: int = Field(default=3, description="Beam width for search")
    strategy: Literal["bfs", "dfs"] = Field(
        default="bfs", description="Search strategy"
    )

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
_reasoning_config: Optional[ReasoningConfig] = None


def get_reasoning_config() -> ReasoningConfig:
    """Get or create the global reasoning configuration instance."""
    global _reasoning_config
    if _reasoning_config is None:
        _reasoning_config = ReasoningConfig()
    return _reasoning_config
