from typing import Literal
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuctionConfig(BaseModel):
    """Configuration for task auctions."""

    bidding_timeout: float = Field(
        default=5.0, description="Timeout for bidding in seconds"
    )
    min_bids: int = Field(default=1, description="Minimum number of bids required")
    max_bids: int = Field(default=10, description="Maximum number of bids allowed")
    tie_breaker: Literal["random", "first", "load"] = Field(
        default="random", description="Strategy to break ties"
    )


class TeamConfig(BaseModel):
    """Configuration for team formation."""

    min_team_size: int = Field(default=2, description="Minimum size of a team")
    max_team_size: int = Field(default=5, description="Maximum size of a team")
    capability_threshold: float = Field(
        default=0.5, description="Minimum capability score threshold"
    )
    leader_selection: Literal["capability", "load", "random"] = Field(
        default="capability", description="Strategy for selecting team leader"
    )


class SwarmConfig(BaseSettings):
    """Configuration for the swarm intelligence system."""

    model_config = SettingsConfigDict(
        env_prefix="SWARM_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    auction: AuctionConfig = Field(default_factory=lambda: AuctionConfig())
    team: TeamConfig = Field(default_factory=lambda: TeamConfig())
    pheromone_decay_rate: float = Field(
        default=0.1, description="Rate of pheromone decay"
    )
    enable_auto_healing: bool = Field(
        default=True, description="Enable self-healing mechanism"
    )


# Global instance
_swarm_config: SwarmConfig | None = None


def get_swarm_config() -> SwarmConfig:
    """Get or create the global swarm configuration instance."""
    global _swarm_config
    if _swarm_config is None:
        _swarm_config = SwarmConfig()
    return _swarm_config
