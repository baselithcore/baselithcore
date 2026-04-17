"""Parse raw widget dicts into typed ``CanvasWidget`` instances.

Accepts the A2UI-style JSON payload sent by tools and the dashboard REST
endpoint. Unknown widget types raise ``CanvasWidgetError`` so callers can
surface a 400 instead of silently dropping data.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .surface import (
    CanvasButton,
    CanvasImage,
    CanvasList,
    CanvasText,
    CanvasWidget,
)
from .widgets_extra import (
    CanvasChart,
    CanvasDivider,
    CanvasForm,
    CanvasProgress,
    CanvasTable,
    FormField,
)


class CanvasWidgetError(ValueError):
    """Raised when an incoming widget payload cannot be parsed."""


def _as_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CanvasWidgetError(
            f"widget payload must be an object, got {type(value).__name__}"
        )
    return value


def build_widget(raw: Any) -> CanvasWidget:
    """Convert a raw dict into a typed ``CanvasWidget`` (recursive)."""
    data = _as_dict(raw)
    wtype = data.get("type")
    if wtype == "text":
        return _build(CanvasText, data)
    if wtype == "button":
        return _build(CanvasButton, data)
    if wtype == "image":
        return _build(CanvasImage, data)
    if wtype == "list":
        items_raw = data.get("items") or []
        if not isinstance(items_raw, list):
            raise CanvasWidgetError("list.items must be an array")
        items = [build_widget(item) for item in items_raw]
        payload = {**data, "items": items}
        return _build(CanvasList, payload)
    if wtype == "form":
        fields_raw = data.get("fields") or []
        if not isinstance(fields_raw, list):
            raise CanvasWidgetError("form.fields must be an array")
        fields = [_build(FormField, _as_dict(f)) for f in fields_raw]
        payload = {**data, "fields": fields}
        return _build(CanvasForm, payload)
    if wtype == "table":
        return _build(CanvasTable, data)
    if wtype == "chart":
        return _build(CanvasChart, data)
    if wtype == "progress":
        return _build(CanvasProgress, data)
    if wtype == "divider":
        return _build(CanvasDivider, data)
    raise CanvasWidgetError(f"unsupported widget type: {wtype!r}")


def build_widgets(raw: Any) -> list[CanvasWidget]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CanvasWidgetError("widgets must be an array")
    return [build_widget(w) for w in raw]


def _build(model: Any, data: dict[str, Any]) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise CanvasWidgetError(str(exc)) from exc


__all__ = ["build_widget", "build_widgets", "CanvasWidgetError"]
