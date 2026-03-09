"""
Prioritization Configuration.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PrioritizationConfig(BaseSettings):
    """
    Configuration for task prioritization scoring weights.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="PRIORITIZATION_",
        populate_by_name=True,
    )

    weight_urgency: float = Field(default=0.25, alias="WEIGHT_URGENCY")
    weight_importance: float = Field(default=0.30, alias="WEIGHT_IMPORTANCE")
    weight_effort: float = Field(default=0.15, alias="WEIGHT_EFFORT")
    weight_deadline: float = Field(default=0.20, alias="WEIGHT_DEADLINE")
    weight_dependencies: float = Field(default=0.10, alias="WEIGHT_DEPENDENCIES")
