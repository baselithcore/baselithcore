"""BacklogPlanner stores the plan and hands off to answer generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from core.chat.workflow_planner import BacklogPlanner


def _state() -> MagicMock:
    state = MagicMock()
    state.rag_only = False
    state.plugin_data = {}
    return state


def test_plan_is_stored_without_touching_the_graph() -> None:
    plan = SimpleNamespace(user_stories=[SimpleNamespace(title="t")])
    planner = MagicMock()
    planner.generate_plan.return_value = plan
    state = _state()
    BacklogPlanner(SimpleNamespace(project_planner=planner)).plan_backlog(state)
    assert state.plugin_data["project_plan"] is plan
    assert state.next_action == "generate_answer"


def test_planner_error_clears_plan() -> None:
    planner = MagicMock()
    planner.generate_plan.side_effect = RuntimeError("boom")
    state = _state()
    BacklogPlanner(SimpleNamespace(project_planner=planner)).plan_backlog(state)
    assert state.plugin_data["project_plan"] is None
    assert state.next_action == "generate_answer"
