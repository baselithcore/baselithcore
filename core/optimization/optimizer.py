"""
Autonomous Prompt Engineering and Optimization.

Implements the 'Active Learning Loop' by closing the gap between
real-world feedback and agent behavior. Analyzes negative performance
signals and uses LLM meta-reasoning to auto-generate and optionally
apply prompt refinements (Self-Correction/Meta-Optimization).
"""

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, Callable, Awaitable, List, Optional

if TYPE_CHECKING:
    from core.services.llm import LLMService

from dataclasses import dataclass

from core.learning.feedback import FeedbackCollector

logger = get_logger(__name__)

# Callback type: async (agent_id, new_prompt) -> success
_ApplyFn = Callable[[str, str], Awaitable[bool]]


@dataclass
class OptimizationSuggestion:
    """A suggestion for improving agent performance."""

    agent_id: str
    issue_type: str  # e.g., "low_score", "slow_response"
    suggestion: str
    confidence: float


@dataclass
class TuneResult:
    """Result of an auto_tune() call."""

    agent_id: str
    suggestion: str
    applied: bool = False
    previous_score: float = 0.0


class PromptOptimizer:
    """
    Meta-cognition engine for behavioral refinement.

    Monitors system-wide performance metrics and identifies suboptimal
    agent performance. Leverages the FeedbackCollector to extract failure
    patterns and synthesizes optimized system instructions, enabling
    continuous evolution of agent reliability.
    """

    def __init__(self, feedback_collector: FeedbackCollector):
        self.feedback_collector = feedback_collector
        self._llm_service: Optional["LLMService"] = None
        self._history: List[dict] = []

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def analyze_performance(
        self, threshold: float = 0.5
    ) -> List[OptimizationSuggestion]:
        """
        Analyze all agents and return suggestions for those performing below threshold.
        """
        suggestions = []

        # Get all feedback items via the public API
        all_feedback = await self.feedback_collector.get_all_feedback()
        agent_ids = {f.agent_id for f in all_feedback}

        for agent_id in agent_ids:
            stats = await self.feedback_collector.get_agent_performance(agent_id)
            avg_score = stats.get("average_score", 1.0)
            count = stats.get("count", 0)

            if count > 0 and avg_score < threshold:
                logger.info(
                    f"Agent {agent_id} is underperforming (score: {avg_score:.2f})"
                )

                # Retrieve negative feedback comments for context
                negative_feedback = [
                    f
                    for f in all_feedback
                    if f.agent_id == agent_id and f.score < threshold
                ]
                comments = [f.comment for f in negative_feedback if f.comment]

                suggestion_text = f"Review prompts for {agent_id}. "
                if comments:
                    suggestion_text += f"Users reported: {'; '.join(comments[:3])}..."

                suggestions.append(
                    OptimizationSuggestion(
                        agent_id=agent_id,
                        issue_type="low_score",
                        suggestion=suggestion_text,
                        confidence=0.8,
                    )
                )

        return suggestions

    async def auto_tune(
        self,
        agent_id: str,
        apply_fn: Optional["_ApplyFn"] = None,
        dry_run: bool = True,
    ) -> Optional["TuneResult"]:
        """Automated prompt tuning using LLM based on negative feedback.

        Args:
            agent_id: The ID of the agent to tune.
            apply_fn: Callback ``async (agent_id, new_prompt) -> bool`` that
                      actually persists the new prompt.  When *None*, the
                      suggestion is returned but never applied.
            dry_run: If *True* (default) the suggestion is generated but
                     ``apply_fn`` is **not** called, even if provided.

        Returns:
            A ``TuneResult`` with the suggestion and whether it was applied,
            or *None* if there was not enough data to generate a suggestion.
        """
        if not self.llm_service:
            logger.warning("LLM service not available for auto-tuning")
            return None

        # 1. Gather context
        stats = await self.feedback_collector.get_agent_performance(agent_id)
        all_feedback = await self.feedback_collector.get_all_feedback()
        negative_feedback = [
            f for f in all_feedback if f.agent_id == agent_id and f.score < 0.6
        ]

        if not negative_feedback:
            logger.info(f"Insufficient negative feedback to tune {agent_id}")
            return None

        comments = [f.comment for f in negative_feedback if f.comment]
        feedback_summary = "\n".join(f"- {c}" for c in comments[:5])

        # 2. Construct Meta-Prompt
        prompt = (
            f"You are an expert Prompt Engineer. An AI agent ('{agent_id}') is "
            f"underperforming.\n\n"
            f"User Feedback & Issues:\n{feedback_summary}\n\n"
            f"Performance Stats:\n"
            f"Average Score: {stats.get('average_score', 0):.2f}\n\n"
            f"Task:\n"
            f"Generate a refined, robust 'System Prompt' instruction that "
            f"specifically addresses these criticisms.\n"
            f"The prompt should be directive, professional, and fix the "
            f"identified behavioral gaps."
        )

        try:
            suggestion = await self.llm_service.generate_response(prompt)
            logger.info(f"Generated prompt optimization for {agent_id}")

            applied = False
            if apply_fn and not dry_run:
                try:
                    applied = await apply_fn(agent_id, suggestion)
                    if applied:
                        logger.info(f"Applied optimization for {agent_id}")
                    else:
                        logger.warning(f"apply_fn returned False for {agent_id}")
                except Exception as apply_err:
                    logger.error(f"Failed to apply optimization: {apply_err}")

            self._history.append(
                {
                    "agent_id": agent_id,
                    "suggestion": suggestion,
                    "applied": applied,
                    "dry_run": dry_run,
                }
            )

            return TuneResult(
                agent_id=agent_id,
                suggestion=suggestion,
                applied=applied,
                previous_score=stats.get("average_score", 0.0),
            )

        except Exception as e:
            logger.error(f"Auto-tune failed: {e}")
            return None

    def get_history(self) -> list:
        """Return the list of all optimization attempts (most recent last)."""
        return list(self._history)
