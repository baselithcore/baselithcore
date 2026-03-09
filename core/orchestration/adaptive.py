"""
Fast/Slow Adaptive Control.

Implements the SwiftSage pattern for System 1/2 thinking.
Routes queries to fast or slow processing paths based on complexity.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, Optional

logger = get_logger(__name__)


class ProcessingPath(Enum):
    """Processing path selection."""

    FAST = "fast"  # System 1: Quick, heuristic-based
    SLOW = "slow"  # System 2: Deliberate, reasoning-based


@dataclass
class ComplexitySignals:
    """Signals used to estimate query complexity."""

    word_count: int = 0
    has_technical_terms: bool = False
    has_multi_step: bool = False
    requires_reasoning: bool = False
    estimated_difficulty: float = 0.5

    @property
    def complexity_score(self) -> float:
        """Calculate overall complexity score (0-1)."""
        score = 0.0

        # Word count contribution
        if self.word_count > 50:
            score += 0.2
        elif self.word_count > 20:
            score += 0.1

        # Technical terms
        if self.has_technical_terms:
            score += 0.2

        # Multi-step
        if self.has_multi_step:
            score += 0.25

        # Reasoning required
        if self.requires_reasoning:
            score += 0.25

        return min(1.0, score + (self.estimated_difficulty * 0.1))


@dataclass
class AdaptiveConfig:
    """Configuration for adaptive routing."""

    fast_threshold: float = 0.3
    """Queries with complexity below this use fast path."""

    slow_threshold: float = 0.6
    """Queries with complexity above this use slow path."""

    enable_hybrid: bool = True
    """Allow hybrid approach for mid-complexity queries."""

    cache_simple_responses: bool = True
    """Cache responses for simple, repetitive queries."""


class AdaptiveController:
    """
    System 1/2 switching for query processing efficiency.

    Implements the SwiftSage pattern:
    - Fast path (System 1): For simple, factual queries
    - Slow path (System 2): For complex, reasoning queries
    - Hybrid: Start fast, escalate if needed

    This reduces latency and cost for simple queries while
    maintaining quality for complex ones.

    Example:
        >>> controller = AdaptiveController()
        >>> path = await controller.route("What time is it?")
        >>> # path == ProcessingPath.FAST
        >>> path = await controller.route("Explain quantum entanglement...")
        >>> # path == ProcessingPath.SLOW
    """

    def __init__(
        self,
        config: Optional[AdaptiveConfig] = None,
        llm_service: Optional[Any] = None,
    ):
        """
        Initialize adaptive controller.

        Args:
            config: Routing configuration
            llm_service: Optional LLM for complexity estimation
        """
        self.config = config or AdaptiveConfig()
        self._llm_service = llm_service
        self._complexity_cache: Dict[str, float] = {}

        # Technical terms that suggest complexity
        self._technical_markers = {
            "implement",
            "algorithm",
            "architecture",
            "optimization",
            "performance",
            "debug",
            "refactor",
            "integrate",
            "deploy",
            "configure",
            "analyze",
            "design",
            "evaluate",
            "compare",
        }

        # Multi-step markers
        self._multistep_markers = {
            "first",
            "then",
            "after",
            "next",
            "finally",
            "step",
            "1.",
            "2.",
            "3.",
            "and then",
            "followed by",
        }

        # Reasoning markers
        self._reasoning_markers = {
            "why",
            "how",
            "explain",
            "reason",
            "because",
            "analyze",
            "compare",
            "evaluate",
            "consider",
            "trade-off",
            "best",
            "should",
            "recommend",
            "pros and cons",
        }

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

    async def route(self, query: str) -> ProcessingPath:
        """
        Determine optimal processing path for a query.

        Args:
            query: The user query

        Returns:
            ProcessingPath indicating fast or slow
        """
        signals = self._analyze_complexity(query)

        if signals.complexity_score < self.config.fast_threshold:
            return ProcessingPath.FAST
        elif signals.complexity_score > self.config.slow_threshold:
            return ProcessingPath.SLOW
        else:
            # Mid-range: use fast but with monitoring
            return ProcessingPath.FAST

    async def route_with_fallback(
        self,
        query: str,
        fast_handler: Callable,
        slow_handler: Callable,
    ) -> tuple[Any, ProcessingPath]:
        """
        Route query and execute with automatic fallback.

        If fast path fails or produces low-confidence result,
        automatically falls back to slow path.

        Args:
            query: User query
            fast_handler: Async handler for fast path
            slow_handler: Async handler for slow path

        Returns:
            Tuple of (result, path_used)
        """
        path = await self.route(query)

        if path == ProcessingPath.SLOW:
            result = await slow_handler(query)
            return result, ProcessingPath.SLOW

        # Try fast path
        try:
            result = await fast_handler(query)

            # Check if result seems adequate
            if self._is_adequate_response(result):
                return result, ProcessingPath.FAST

            # Fallback to slow
            logger.info("Fast path result inadequate, falling back to slow")
            result = await slow_handler(query)
            return result, ProcessingPath.SLOW

        except Exception as e:
            logger.warning(f"Fast path failed: {e}, falling back to slow")
            result = await slow_handler(query)
            return result, ProcessingPath.SLOW

    def _analyze_complexity(self, query: str) -> ComplexitySignals:
        """
        Analyze query complexity signals.

        Heuristically evaluates the query for technical terms, multi-step
        indicators, and reasoning requirements.

        Args:
            query: The user input text.

        Returns:
            ComplexitySignals: An object containing collected complexity flags.
        """
        query_lower = query.lower()
        words = query_lower.split()

        signals = ComplexitySignals(word_count=len(words))

        # Check for technical terms
        signals.has_technical_terms = any(
            marker in query_lower for marker in self._technical_markers
        )

        # Check for multi-step indicators
        signals.has_multi_step = any(
            marker in query_lower for marker in self._multistep_markers
        )

        # Check for reasoning requirements
        signals.requires_reasoning = any(
            marker in query_lower for marker in self._reasoning_markers
        )

        # Base difficulty from structure
        if "?" in query:
            signals.estimated_difficulty += 0.1
        if len(query) > 200:
            signals.estimated_difficulty += 0.2

        return signals

    def _is_adequate_response(self, result: Any) -> bool:
        """
        Check if a response seems adequate (heuristic).

        Evaluates the result of a fast path execution to determine if it
        meets a minimum quality threshold.

        Args:
            result: The output from a handler.

        Returns:
            bool: True if the response is considered sufficient.
        """
        if result is None:
            return False

        if isinstance(result, str):
            # Very short responses might be inadequate
            if len(result) < 10:
                return False
            # Error indicators
            if "error" in result.lower() or "cannot" in result.lower():
                return False

        return True

    def get_routing_stats(self) -> Dict[str, Any]:
        """
        Get routing configuration and stats.

        Returns:
            Dict[str, Any]: A summary of the controller's runtime statistics.
        """
        return {
            "fast_threshold": self.config.fast_threshold,
            "slow_threshold": self.config.slow_threshold,
            "enable_hybrid": self.config.enable_hybrid,
            "technical_markers_count": len(self._technical_markers),
        }
