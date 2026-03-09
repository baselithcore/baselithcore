"""
Hypothesis Generator

Generates hypotheses for unknown or incomplete information.
"""

from core.observability.logging import get_logger
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum

logger = get_logger(__name__)


class ConfidenceLevel(Enum):
    """Confidence levels for hypotheses."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    SPECULATIVE = "speculative"


@dataclass
class Hypothesis:
    """A generated hypothesis."""

    statement: str
    confidence: ConfidenceLevel
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    @property
    def is_testable(self) -> bool:
        """Check if hypothesis can be tested."""
        return len(self.assumptions) > 0


class HypothesisGenerator:
    """
    Generates hypotheses for exploration and discovery.

    Features:
    - Evidence-based hypothesis generation
    - Confidence scoring
    - Assumption identification
    """

    def __init__(self, llm_service=None):
        """
        Initialize generator.

        Args:
            llm_service: Optional LLM for generation
        """
        self._llm_service = llm_service

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                logger.debug("LLM service not available for hypothesis generation")
        return self._llm_service

    async def generate(
        self,
        context: str,
        known_facts: Optional[List[str]] = None,
        unknowns: Optional[List[str]] = None,
        max_hypotheses: int = 3,
    ) -> List[Hypothesis]:
        """
        Generate hypotheses based on context.

        Args:
            context: The context or question
            known_facts: Known facts to consider
            unknowns: Unknown aspects to address
            max_hypotheses: Maximum hypotheses to generate

        Returns:
            List of Hypothesis objects
        """
        known = known_facts or []
        gaps = unknowns or []

        hypotheses = []

        # Generate using LLM if available
        if self.llm_service:
            hypotheses = await self._generate_with_llm(
                context, known, gaps, max_hypotheses
            )
        else:
            hypotheses = self._generate_simple(context, gaps, max_hypotheses)

        return hypotheses

    async def _generate_with_llm(
        self,
        context: str,
        known: List[str],
        gaps: List[str],
        max_count: int,
    ) -> List[Hypothesis]:
        """Generate hypotheses using LLM."""
        known_str = "\n".join(f"- {f}" for f in known) if known else "None"
        gaps_str = "\n".join(f"- {g}" for g in gaps) if gaps else "Unknown"

        prompt = f"""Based on this context, generate {max_count} hypotheses.

Context: {context}

Known facts:
{known_str}

Unknowns to address:
{gaps_str}

For each hypothesis provide:
1. The statement
2. Assumptions required
3. How confident (high/medium/low/speculative)

Format as:
HYPOTHESIS: <statement>
ASSUMPTIONS: <comma-separated>
CONFIDENCE: <level>
---"""

        try:
            result = await self.llm_service.generate_response(prompt)
            return self._parse_hypotheses(result)
        except Exception as e:
            logger.warning(f"LLM hypothesis generation failed: {e}")
            return self._generate_simple(context, gaps, max_count)

    def _generate_simple(
        self,
        context: str,
        gaps: List[str],
        max_count: int,
    ) -> List[Hypothesis]:
        """Generate simple hypotheses without LLM."""
        hypotheses = []

        for gap in gaps[:max_count]:
            hypotheses.append(
                Hypothesis(
                    statement=f"The unknown '{gap}' may be related to {context}",
                    confidence=ConfidenceLevel.SPECULATIVE,
                    assumptions=[f"Assumption: {gap} is relevant to the context"],
                )
            )

        if not hypotheses:
            hypotheses.append(
                Hypothesis(
                    statement=f"Further investigation needed for: {context}",
                    confidence=ConfidenceLevel.LOW,
                    assumptions=["More data may reveal patterns"],
                )
            )

        return hypotheses[:max_count]

    def _parse_hypotheses(self, text: str) -> List[Hypothesis]:
        """Parse LLM output into Hypothesis objects."""
        hypotheses = []

        for block in text.split("---"):
            if "HYPOTHESIS:" not in block:
                continue

            try:
                lines = block.strip().split("\n")
                statement = ""
                assumptions = []
                confidence = ConfidenceLevel.MEDIUM

                for line in lines:
                    if line.startswith("HYPOTHESIS:"):
                        statement = line.replace("HYPOTHESIS:", "").strip()
                    elif line.startswith("ASSUMPTIONS:"):
                        assumptions = [
                            a.strip()
                            for a in line.replace("ASSUMPTIONS:", "").split(",")
                        ]
                    elif line.startswith("CONFIDENCE:"):
                        level = line.replace("CONFIDENCE:", "").strip().lower()
                        if level in ["high", "medium", "low", "speculative"]:
                            confidence = ConfidenceLevel(level)

                if statement:
                    hypotheses.append(
                        Hypothesis(
                            statement=statement,
                            confidence=confidence,
                            assumptions=assumptions,
                        )
                    )
            except Exception as e:
                logger.warning(f"Failed to parse hypothesis block: {e}")
                continue

        return hypotheses
