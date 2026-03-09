from typing import Dict
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorldModelConfig(BaseSettings):
    """Configuration for World Model and Predictive Planning."""

    model_config = SettingsConfigDict(env_prefix="WORLD_MODEL_")

    # Risk Assessor Weights
    risk_weights: Dict[str, float] = Field(
        default={
            "action_type": 0.3,
            "reversibility": 0.25,
            "state_delta": 0.25,
            "uncertainty": 0.2,
        },
        description="Weights for risk factors",
    )

    # MCTS Configuration
    mcts_max_iterations: int = Field(default=100, description="Max MCTS iterations")
    mcts_max_depth: int = Field(default=10, description="Max MCTS tree depth")
    mcts_exploration_weight: float = Field(
        default=1.41, description="UCB1 exploration weight"
    )
    mcts_simulation_depth: int = Field(default=5, description="Random simulation depth")
    mcts_time_limit: float = Field(
        default=5.0, description="Time limit for MCTS in seconds"
    )

    # Rollback Configuration
    rollback_enable_checkpoints: bool = Field(
        default=True, description="Enable state checkpoints"
    )
    rollback_max_checkpoint_age: int = Field(
        default=10, description="Max actions before checkpoint expires"
    )


def get_world_model_config() -> WorldModelConfig:
    return WorldModelConfig()
