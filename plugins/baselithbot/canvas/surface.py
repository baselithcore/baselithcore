"""In-memory canvas + widget primitives backing the Live Canvas surface.

The renderer (``A2UIRenderer``) consumes ``CanvasWidget`` trees and emits
``A2UIMessage`` envelopes consumable by any front-end implementing the
A2UI protocol (web, mobile, terminal).
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from plugins.baselithbot.canvas.widgets_extra import (
    CanvasChart,
    CanvasDivider,
    CanvasForm,
    CanvasProgress,
    CanvasTable,
)
from pydantic import BaseModel, Field


class CanvasText(BaseModel):
    type: Literal["text"] = "text"
    id: str = Field(default_factory=lambda: f"text-{uuid.uuid4().hex[:8]}")
    content: str
    style: dict[str, Any] = Field(default_factory=dict)


class CanvasButton(BaseModel):
    type: Literal["button"] = "button"
    id: str = Field(default_factory=lambda: f"btn-{uuid.uuid4().hex[:8]}")
    label: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class CanvasImage(BaseModel):
    type: Literal["image"] = "image"
    id: str = Field(default_factory=lambda: f"img-{uuid.uuid4().hex[:8]}")
    url: str | None = None
    base64_png: str | None = None
    alt: str = ""


class CanvasList(BaseModel):
    type: Literal["list"] = "list"
    id: str = Field(default_factory=lambda: f"list-{uuid.uuid4().hex[:8]}")
    items: list[CanvasWidget] = Field(default_factory=list)
    ordered: bool = False


CanvasWidget = (
    CanvasText
    | CanvasButton
    | CanvasImage
    | CanvasList
    | CanvasForm
    | CanvasTable
    | CanvasChart
    | CanvasProgress
    | CanvasDivider
)
CanvasList.model_rebuild()


class CanvasSurface:
    """Append-only surface holding the current widget tree."""

    def __init__(self, surface_id: str | None = None) -> None:
        self.surface_id = surface_id or f"surface-{uuid.uuid4().hex[:8]}"
        self._widgets: list[CanvasWidget] = []
        self._created_at = time.time()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def widgets(self) -> list[CanvasWidget]:
        return list(self._widgets)

    def add(self, widget: CanvasWidget) -> CanvasWidget:
        self._widgets.append(widget)
        self._revision += 1
        return widget

    def extend(self, widgets: list[CanvasWidget]) -> list[CanvasWidget]:
        for widget in widgets:
            self._widgets.append(widget)
        if widgets:
            self._revision += 1
        return widgets

    def clear(self) -> None:
        self._widgets.clear()
        self._revision += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "revision": self._revision,
            "created_at": self._created_at,
            "widgets": [w.model_dump() for w in self._widgets],
        }


__all__ = [
    "CanvasSurface",
    "CanvasWidget",
    "CanvasText",
    "CanvasButton",
    "CanvasImage",
    "CanvasList",
]
