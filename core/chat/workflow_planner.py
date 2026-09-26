"""
Backlog planning workflow.

This module provides the BacklogPlanner class for generating project plans
from document analysis. External sync functionality (e.g., to issue trackers)
should be implemented via plugins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.chat.agent_state import AgentState
from core.config import get_app_config
from core.observability import telemetry
from core.observability.logging import get_logger

PROJECT_PLANNER_ENABLE_TEST_CASES = get_app_config().project_planner_enable_test_cases

if TYPE_CHECKING:
    from core.chat.service import ChatService


logger = get_logger(__name__)


class BacklogPlanner:
    """Generate project plans from document analysis.

    This class handles the core planning logic. External sync (e.g., to external issue trackers
    or other issue trackers) should be implemented via plugins that hook into
    the plan generation lifecycle.
    """

    def __init__(self, service: ChatService) -> None:
        self.service = service

    def plan_backlog(self, state: AgentState) -> None:
        """Generate a project plan from the current context.

        Args:
            state: The current agent state containing context and history.
        """
        if state.rag_only:
            state.log("planner:skipped_rag_only")
            state.plugin_data["project_plan"] = None
            state.next_action = "generate_answer"
            return

        planner = getattr(self.service, "project_planner", None)
        if planner is None:
            state.log("planner:disabled")
            state.plugin_data["project_plan"] = None
            state.next_action = "generate_answer"
            return

        try:
            plan = planner.generate_plan(
                query=state.user_query,
                context=state.context,
                history_text=state.history_text,
            )
        except Exception:
            telemetry.increment("planner.error")
            state.log("planner:error")
            state.plugin_data["project_plan"] = None
        else:
            telemetry.increment("planner.generated")
            state.plugin_data["project_plan"] = plan

        state.next_action = "generate_answer"


__all__ = ["BacklogPlanner"]
