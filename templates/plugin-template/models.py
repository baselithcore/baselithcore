"""
Data models for MyPlugin.

Replace 'MyItem' and 'MyPlugin' with your domain-specific model names.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MyItem(BaseModel):
    """
    Example model. Replace with your domain models.
    """

    id: str = Field(..., description="Unique identifier")
    name: str = Field(..., description="Display name")
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "item_001",
                "name": "Example Item",
                "data": {"key": "value"},
            }
        }
    )
