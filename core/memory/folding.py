"""
Proactive Context Folding (AgentFold).

This module implements the AgentFold pattern, which proactively compresses
older interaction history while maintaining the integrity of recent
messages. This helps prevent context-window saturation and the
"lost-in-the-middle" phenomenon.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass
from typing import Any, List, Optional

from .types import MemoryItem

logger = get_logger(__name__)


@dataclass
class FoldingConfig:
    """Configuration for context folding."""

    keep_latest_n: int = 3
    """Number of recent interactions to keep verbatim."""

    fold_threshold_chars: int = 2000
    """Fold when total context exceeds this character count."""

    summary_max_chars: int = 500
    """Maximum characters for folded summaries."""

    preserve_system_messages: bool = True
    """Whether to preserve system/instruction messages."""


class ContextFolder:
    """
    Manages the proactive folding of conversation history.

    Keeps the most recent N messages intact while recursively
    summarizing older blocks of the history into semantic 'folds'.
    This allows agents to maintain a long conversational tail without
    exceeding token limits.
    Example:
        >>> folder = ContextFolder(llm_service=llm)
        >>> history = [MemoryItem(...), MemoryItem(...), ...]
        >>> folded = await folder.fold(history)
        >>> # Use folded context in prompts
    """

    def __init__(
        self,
        config: Optional[FoldingConfig] = None,
        llm_service: Optional[Any] = None,
    ):
        """
        Initialize context folder.

        Args:
            config: Folding configuration
            llm_service: Optional LLM for intelligent summarization
        """
        self.config = config or FoldingConfig()
        self._llm_service = llm_service

    @property
    def llm_service(self) -> Optional[Any]:
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                pass
        return self._llm_service

    async def fold(self, history: List[MemoryItem]) -> str:
        """
        Fold interaction history into compressed context.

        Recent messages are kept verbatim, older messages are summarized.

        Args:
            history: List of MemoryItem representing interaction history

        Returns:
            Formatted context string with folded summaries
        """
        if not history:
            return ""

        # Split into recent (keep) and older (fold)
        keep_count = self.config.keep_latest_n

        if keep_count <= 0:
            to_keep = []
            to_fold = history
        else:
            to_keep = history[-keep_count:] if len(history) > keep_count else history
            to_fold = history[:-keep_count] if len(history) > keep_count else []

        parts = []

        # Fold older messages
        if to_fold:
            folded_summary = await self._create_folded_summary(to_fold)
            if folded_summary:
                parts.append(f"[Previous context: {folded_summary}]\n")

        # Keep recent messages verbatim
        for item in to_keep:
            role = item.metadata.get("role", "user")
            parts.append(f"[{role}]: {item.content}\n")

        return "".join(parts)

    async def fold_if_needed(
        self,
        history: List[MemoryItem],
    ) -> tuple[str, bool]:
        """
        Fold only if context exceeds threshold.

        Args:
            history: Interaction history

        Returns:
            Tuple of (context string, was_folded bool)
        """
        # Calculate current size
        total_chars = sum(len(item.content) for item in history)

        if total_chars <= self.config.fold_threshold_chars:
            # No folding needed, return verbatim
            parts = []
            for item in history:
                role = item.metadata.get("role", "user")
                parts.append(f"[{role}]: {item.content}\n")
            return "".join(parts), False

        # Folding needed
        return await self.fold(history), True

    async def _create_folded_summary(self, items: List[MemoryItem]) -> Optional[str]:
        """Create a summary of folded items."""
        if not items:
            return None

        contents = [item.content for item in items]

        if self.llm_service:
            try:
                prompt = f"""Summarize the following conversation history into a brief context summary.
Focus on key decisions, facts, and user preferences mentioned.
Keep the summary under {self.config.summary_max_chars} characters.

Conversation:
{chr(10).join(f"- {c}" for c in contents)}

Summary:"""

                result = await self.llm_service.generate_response(prompt)
                # Truncate if needed
                if len(result) > self.config.summary_max_chars:
                    result = result[: self.config.summary_max_chars - 3] + "..."
                return result
            except Exception as e:
                logger.warning(f"LLM folding failed: {e}")

        # Fallback: simple extraction of key phrases
        combined = " | ".join(contents)
        if len(combined) > self.config.summary_max_chars:
            combined = combined[: self.config.summary_max_chars - 3] + "..."
        return combined

    def estimate_token_savings(
        self,
        history: List[MemoryItem],
    ) -> dict:
        """
        Estimate token savings from folding.

        Args:
            history: Interaction history

        Returns:
            Dict with original_chars, folded_chars, savings_percent
        """
        original_chars = sum(len(item.content) for item in history)
        keep_count = self.config.keep_latest_n
        to_keep = history[-keep_count:] if len(history) > keep_count else history
        to_fold = history[:-keep_count] if len(history) > keep_count else []

        kept_chars = sum(len(item.content) for item in to_keep)
        folded_chars = (
            min(
                sum(len(item.content) for item in to_fold),
                self.config.summary_max_chars,
            )
            if to_fold
            else 0
        )

        estimated_total = kept_chars + folded_chars
        savings = max(0, original_chars - estimated_total)

        return {
            "original_chars": original_chars,
            "estimated_chars": estimated_total,
            "savings_chars": savings,
            "savings_percent": (savings / original_chars * 100)
            if original_chars > 0
            else 0,
        }
