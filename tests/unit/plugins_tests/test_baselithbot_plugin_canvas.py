"""Unit tests for the Baselithbot plugin — canvas widgets and A2UI rendering."""

from __future__ import annotations


def test_canvas_render_a2ui_envelope() -> None:
    from plugins.baselithbot.canvas import A2UIRenderer, CanvasSurface, CanvasText

    surface = CanvasSurface()
    surface.add(CanvasText(content="hello"))
    msg = A2UIRenderer().render(surface)
    assert msg.protocol == "a2ui"
    assert msg.surface_id == surface.surface_id
    assert msg.widgets and msg.widgets[0]["type"] == "text"


def test_canvas_builders_parse_nested_list() -> None:
    from plugins.baselithbot.canvas import CanvasList, build_widget

    parsed = build_widget(
        {
            "type": "list",
            "ordered": True,
            "items": [
                {"type": "text", "content": "hi"},
                {
                    "type": "list",
                    "items": [{"type": "text", "content": "nested"}],
                },
            ],
        }
    )
    assert isinstance(parsed, CanvasList)
    assert parsed.ordered is True
    assert len(parsed.items) == 2
    inner = parsed.items[1]
    assert isinstance(inner, CanvasList)
    assert inner.items[0].content == "nested"  # type: ignore[union-attr]


def test_canvas_builders_handle_all_widget_types() -> None:
    from plugins.baselithbot.canvas import build_widgets

    widgets = build_widgets(
        [
            {"type": "text", "content": "hi"},
            {"type": "button", "label": "Go", "action": "ping"},
            {"type": "image", "url": "https://example.com/a.png"},
            {
                "type": "form",
                "submit_action": "submit",
                "fields": [{"name": "x", "label": "X", "type": "text"}],
            },
            {"type": "table", "columns": ["a"], "rows": [[1], [2]]},
            {"type": "chart", "chart_type": "bar", "series": []},
            {"type": "progress", "value": 0.5},
            {"type": "divider"},
        ]
    )
    assert [w.type for w in widgets] == [
        "text",
        "button",
        "image",
        "form",
        "table",
        "chart",
        "progress",
        "divider",
    ]


def test_canvas_builders_reject_unknown_type() -> None:
    import pytest as _pytest

    from plugins.baselithbot.canvas import CanvasWidgetError, build_widget

    with _pytest.raises(CanvasWidgetError):
        build_widget({"type": "does-not-exist"})


def test_canvas_render_tool_supports_extras_and_nested_lists() -> None:
    import asyncio as _asyncio

    from plugins.baselithbot.canvas import CanvasSurface
    from plugins.baselithbot.control.openclaw_tools import (
        build_openclaw_tool_definitions,
    )

    surface = CanvasSurface()
    tools = build_openclaw_tool_definitions(canvas=surface)
    handler = next(
        t["handler"] for t in tools if t["name"] == "baselithbot_canvas_render"
    )

    result = _asyncio.run(
        handler(
            widgets=[
                {
                    "type": "list",
                    "items": [
                        {"type": "text", "content": "inner"},
                        {"type": "progress", "value": 0.3},
                    ],
                },
                {"type": "divider"},
            ],
            clear=True,
        )
    )
    assert result["status"] == "success"
    rendered = result["a2ui"]["widgets"]
    assert rendered[0]["type"] == "list"
    assert len(rendered[0]["items"]) == 2
    assert rendered[0]["items"][1]["type"] == "progress"
    assert rendered[1]["type"] == "divider"


def test_canvas_render_tool_rejects_bad_widget() -> None:
    import asyncio as _asyncio

    from plugins.baselithbot.canvas import CanvasSurface
    from plugins.baselithbot.control.openclaw_tools import (
        build_openclaw_tool_definitions,
    )

    tools = build_openclaw_tool_definitions(canvas=CanvasSurface())
    handler = next(
        t["handler"] for t in tools if t["name"] == "baselithbot_canvas_render"
    )

    result = _asyncio.run(handler(widgets=[{"type": "nope"}]))
    assert result["status"] == "error"
    assert result["tool"] == "canvas_render"


def test_canvas_extra_widgets_serialize() -> None:
    from plugins.baselithbot.canvas import (
        CanvasChart,
        CanvasForm,
        CanvasProgress,
        CanvasTable,
        FormField,
    )

    form = CanvasForm(
        submit_action="submit",
        fields=[FormField(name="email", type="email", required=True)],
    )
    table = CanvasTable(columns=["a", "b"], rows=[[1, 2], [3, 4]])
    chart = CanvasChart(chart_type="bar", series=[{"label": "s", "data": [1]}])
    progress = CanvasProgress(value=0.42, label="loading")

    for widget in (form, table, chart, progress):
        dumped = widget.model_dump()
        assert dumped["id"]
        assert dumped["type"]
