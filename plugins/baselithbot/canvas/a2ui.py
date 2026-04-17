"""A2UI envelope schema and renderer."""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from .surface import CanvasSurface


class A2UIMessage(BaseModel):
    """Versioned A2UI envelope shipped to clients."""

    protocol: str = "a2ui"
    version: str = "1.0"
    surface_id: str
    revision: int
    timestamp: float = Field(default_factory=time.time)
    widgets: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2UIRenderer:
    """Convert a ``CanvasSurface`` snapshot into an ``A2UIMessage``."""

    def render(self, surface: CanvasSurface, **metadata: Any) -> A2UIMessage:
        snap = surface.snapshot()
        return A2UIMessage(
            surface_id=snap["surface_id"],
            revision=snap["revision"],
            widgets=snap["widgets"],
            metadata=metadata,
        )


__all__ = ["A2UIMessage", "A2UIRenderer"]
