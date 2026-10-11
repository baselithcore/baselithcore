"""Browser Agent: real EXTRACT payloads + correct MCP autonomy categories.

Two regressions are covered:

* ``execute_task`` must surface the model's *actual* extracted values
  (``action.data``), not a fabricated ``"[extracted from <url>]"`` placeholder.
* Action tools (click/type/execute_task) must register as ``mutating`` so the
  orchestrator's autonomy gate can require approval; reads stay ``read_only``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from baselith.plugins.browser_agent.agent import BrowserAgent
from baselith.plugins.browser_agent.tools import build_browser_tool_definitions
from baselith.plugins.browser_agent.types import (
    BrowserAction,
    BrowserActionType,
    PageState,
)


def _page_state() -> PageState:
    return PageState(
        url="https://example.com/list",
        title="List",
        screenshot_base64="Zm9v",
        viewport_width=1280,
        viewport_height=720,
        visible_text="Repo A 1234\nRepo B 567",
    )


async def test_execute_task_returns_real_extracted_data() -> None:
    agent = BrowserAgent(headless=True)
    agent._page = object()  # skip start()
    agent.get_page_state = AsyncMock(return_value=_page_state())  # type: ignore[method-assign]

    real = {"titles": ["Repo A", "Repo B"], "stars": [1234, 567]}
    agent.decide_next_action = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            BrowserAction(
                action_type=BrowserActionType.EXTRACT, data=real, reasoning="found"
            ),
            BrowserAction(action_type=BrowserActionType.DONE, reasoning="done"),
        ]
    )

    result = await agent.execute_task("extract the repos")

    assert result.success is True
    assert result.extracted_data == real
    # No fabricated placeholder must ever leak into the payload.
    assert all("[extracted from" not in str(v) for v in result.extracted_data.values())


async def test_extract_without_data_does_not_fabricate() -> None:
    agent = BrowserAgent(headless=True)
    agent._page = object()
    agent.get_page_state = AsyncMock(return_value=_page_state())  # type: ignore[method-assign]
    agent.decide_next_action = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            BrowserAction(
                action_type=BrowserActionType.EXTRACT, value="titles", reasoning="x"
            ),
            BrowserAction(action_type=BrowserActionType.DONE, reasoning="done"),
        ]
    )

    result = await agent.execute_task("extract")

    # A dataless extract yields nothing rather than a placeholder string.
    assert result.extracted_data == {}


def test_acting_tools_reach_the_approval_gate_reads_do_not() -> None:
    """Navigation is an outbound fetch, so it is an external side effect too."""
    cats = {d["name"]: d["category"] for d in build_browser_tool_definitions()}
    assert cats["browser_click"] == "external_side_effect"
    assert cats["browser_type"] == "external_side_effect"
    assert cats["browser_execute_task"] == "external_side_effect"
    assert cats["browser_navigate"] == "external_side_effect"
    assert cats["browser_screenshot"] == "read_only"
