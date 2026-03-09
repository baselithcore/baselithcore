"""
Backlog planning workflow.

This module provides the BacklogPlanner class for generating project plans
from document analysis. External sync functionality (e.g., to issue trackers)
should be implemented via plugins.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import TYPE_CHECKING

from core.chat.agent_state import AgentState
from core.observability import telemetry
from core.config import get_app_config, get_storage_config

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

    def __init__(self, service: "ChatService") -> None:
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

            # --- Duplicate Detection via Graph DB ---
            storage_config = get_storage_config()
            if storage_config.graph_db_enabled:
                try:
                    # Lazy import to avoid circular dependency and abstraction leak
                    from core.graph import graph_db
                    from core.services.graph.agent import GraphService

                    if graph_db.is_enabled():
                        ga = GraphService(graph_db)
                        for story in plan.user_stories:
                            # Note: find_similar_stories is part of GraphService but might be dynamically added or missing type hint
                            if hasattr(ga, "find_similar_stories"):
                                similar = ga.find_similar_stories(  # type: ignore[attr-defined]
                                    story.title, threshold=0.80, limit=3
                                )
                                if similar:
                                    state.log(
                                        f"planner:duplicate_detected:{story.title[:30]}"
                                    )
                                    warning_lines = [
                                        "\n\n> [!WARNING] Possible Duplicate",
                                        "> This story appears similar to existing stories:",
                                    ]
                                    for s in similar:
                                        warning_lines.append(
                                            f"> - **{s['summary']}** ({s['status']}) - Score: {s['score']:.2f}"
                                        )
                                    current_desc = story.description
                                    story._description = (
                                        current_desc + "\n" + "\n".join(warning_lines)
                                    )
                except Exception as e:
                    logger.warning(f"Duplicate detection failed: {e}", exc_info=True)

        state.next_action = "generate_answer"


__all__ = ["BacklogPlanner"]
