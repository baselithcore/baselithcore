"""Live Canvas + A2UI rendering surface (OpenClaw parity)."""

from .a2ui import A2UIMessage, A2UIRenderer
from .builders import CanvasWidgetError, build_widget, build_widgets
from .surface import (
    CanvasButton,
    CanvasImage,
    CanvasList,
    CanvasSurface,
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

__all__ = [
    "CanvasSurface",
    "CanvasWidget",
    "CanvasWidgetError",
    "CanvasText",
    "CanvasButton",
    "CanvasImage",
    "CanvasList",
    "CanvasForm",
    "CanvasTable",
    "CanvasChart",
    "CanvasProgress",
    "CanvasDivider",
    "FormField",
    "A2UIMessage",
    "A2UIRenderer",
    "build_widget",
    "build_widgets",
]
