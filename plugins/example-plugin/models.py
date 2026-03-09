"""Example Plugin Models.

Pydantic models used by the plugin for validation and data structure.
"""

from typing import Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class ExampleItem(BaseModel):
    """Model representing an item in the example plugin."""

    id: Optional[str] = Field(None, description="Unique identifier")
    name: str = Field(..., description="Name of the item")
    created_at: datetime = Field(default_factory=datetime.now)
    tags: list[str] = Field(default_factory=list, description="Associated tags")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class ExampleConfig(BaseModel):
    """Configuration model for the plugin."""

    enable_feature_x: bool = Field(True, description="Enable Feature X")
    max_items: int = Field(100, description="Maximum number of items")
