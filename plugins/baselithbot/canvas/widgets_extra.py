"""Additional A2UI widget primitives: form, table, chart, progress, divider."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field


class FormField(BaseModel):
    name: str
    label: str = ""
    type: Literal["text", "number", "password", "email", "select", "checkbox"] = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    default: Any = None


class CanvasForm(BaseModel):
    type: Literal["form"] = "form"
    id: str = Field(default_factory=lambda: f"form-{uuid.uuid4().hex[:8]}")
    title: str = ""
    submit_action: str
    fields: list[FormField] = Field(default_factory=list)


class CanvasTable(BaseModel):
    type: Literal["table"] = "table"
    id: str = Field(default_factory=lambda: f"table-{uuid.uuid4().hex[:8]}")
    columns: list[str]
    rows: list[list[Any]] = Field(default_factory=list)
    sortable: bool = True


class CanvasChart(BaseModel):
    type: Literal["chart"] = "chart"
    id: str = Field(default_factory=lambda: f"chart-{uuid.uuid4().hex[:8]}")
    chart_type: Literal["line", "bar", "pie", "area"] = "line"
    series: list[dict[str, Any]] = Field(default_factory=list)
    x_axis: str = ""
    y_axis: str = ""


class CanvasProgress(BaseModel):
    type: Literal["progress"] = "progress"
    id: str = Field(default_factory=lambda: f"progress-{uuid.uuid4().hex[:8]}")
    value: float = Field(ge=0.0, le=1.0)
    label: str = ""


class CanvasDivider(BaseModel):
    type: Literal["divider"] = "divider"
    id: str = Field(default_factory=lambda: f"div-{uuid.uuid4().hex[:8]}")
    orientation: Literal["horizontal", "vertical"] = "horizontal"


__all__ = [
    "FormField",
    "CanvasForm",
    "CanvasTable",
    "CanvasChart",
    "CanvasProgress",
    "CanvasDivider",
]
