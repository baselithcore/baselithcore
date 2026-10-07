"""Names are validated as whole strings: no trailing newline, NUL, space or lookalike gets through."""

from __future__ import annotations

import pytest

from core.mcp.client_types import MCPToolInfo
from core.mcp.declarative import make_mcp_tool_fns

BAD = [
    "tool\n",
    "tool\r\n",
    "tool\x00",
    "to\x00ol",
    " tool",
    "tool ",
    "\ttool",
    "ｔｏｏｌ",  # fullwidth "tool"
    "tоol",  # Cyrillic o
]


class _Pool:
    async def call_tool(self, server: str, tool: str, args: dict) -> str:
        return tool


def _tools(*names: str) -> list[MCPToolInfo]:
    return [MCPToolInfo(n, "d", {"type": "object"}) for n in names]


@pytest.mark.parametrize("name", BAD)
def test_a_tool_name_must_match_as_a_whole(name: str) -> None:
    assert (
        make_mcp_tool_fns(_Pool(), "srv", _tools(name), autonomy_category="read_only")
        == []
    )


@pytest.mark.parametrize("name", BAD)
def test_a_server_name_must_match_as_a_whole(name: str) -> None:
    with pytest.raises(ValueError):
        make_mcp_tool_fns(_Pool(), name, _tools("a"), autonomy_category="read_only")


def test_names_equal_after_strip_or_case_are_one_tool_and_the_first_wins() -> None:
    defs = make_mcp_tool_fns(
        _Pool(),
        "srv",
        _tools("a", "a\n", "A", "b", "B", " b"),
        autonomy_category="read_only",
    )
    assert [d.name for d in defs] == ["srv.a", "srv.b"]


async def test_the_registered_and_the_called_name_are_the_same_string() -> None:
    [d] = make_mcp_tool_fns(
        _Pool(), "srv", _tools("Tool_1"), autonomy_category="read_only"
    )
    assert d.name == "srv.Tool_1" and await d.fn() == "Tool_1"
