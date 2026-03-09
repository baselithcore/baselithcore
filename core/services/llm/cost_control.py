"""
Cost control and budget enforcement for LLM operations.

This module provides tools to track token consumption across different
models and providers, ensuring that operations stay within specified
financial or capability-based boundaries.
"""

from core.observability.logging import get_logger
from typing import Optional

logger = get_logger(__name__)


class CostTracker:
    """
    Stateful tracker for LLM token usage.

    Monitors incoming and outgoing tokens and raises defensive errors
    if a predefined limit (max_tokens) is exceeded.
    """

    def __init__(self, max_tokens: Optional[int] = None):
        """
        Initialize the cost tracker.

        Args:
            max_tokens: The absolute limit of tokens allowed for this tracker.
                        If None, tracking is performed without enforcement.
        """
        self.max_tokens = max_tokens
        self.tokens_used = 0

    def track_tokens(self, count: int, model: str = "unknown") -> None:
        """
        Add tokens to the current count and check budget compliance.

        Args:
            count: Number of tokens to add to the tally.
            model: Name of the model used (for logging/debugging).

        Raises:
            BudgetExceededError: If the new total exceeds `max_tokens`.
        """
        from core.services.llm.exceptions import BudgetExceededError

        self.tokens_used += count

        if self.max_tokens and self.tokens_used > self.max_tokens:
            logger.error(
                f"🛑 BUDGET EXCEEDED: Tokens {self.tokens_used} > {self.max_tokens}"
            )
            raise BudgetExceededError(
                f"Token limit exceeded: {self.tokens_used}/{self.max_tokens}"
            )

        logger.debug(f"Tracked {count} tokens for model {model}")

    def get_usage(self) -> dict:
        """
        Retrieve a summary of token usage.

        Returns:
            dict: Contains 'tokens_used', 'max_tokens', and 'remaining'.
        """
        return {
            "tokens_used": self.tokens_used,
            "max_tokens": self.max_tokens,
            "remaining": (
                self.max_tokens - self.tokens_used if self.max_tokens else None
            ),
        }


def estimate_tokens(text: str) -> int:
    """
    Calculate an approximate token count for a given text.

    Uses a reliable heuristic or library-backed approach (like tiktoken)
    via the internal `core.utils.tokens` utility.

    Args:
        text: The source text to analyze.

    Returns:
        int: The estimated number of tokens.
    """
    from core.utils.tokens import estimate_tokens as _estimate

    return _estimate(text)
