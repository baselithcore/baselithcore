"""
Multi-Persona Meta-Cognition Orchestrator.

Implements the "Society of Mind" pattern by coordinating an ensemble of
diverse personas. Manages the synthesis of multiple expert perspectives
through internal debate to produce balanced, high-confidence outputs.
"""

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from core.services.llm import LLMService

from .types import (
    Perspective,
    DebateResult,
    MetaAgentResponse,
    ConsensusLevel,
)
from .ensemble import PersonaEnsemble
from .debate import InternalDebate

logger = get_logger(__name__)


class MultiPersonaAgent:
    """
    Orchestrator for ensemble-based reasoning.

    Leverages a persona ensemble and an internal debate manager to process
    complex queries. This meta-agent doesn't just generate a response, but
    synthesizes a consensus from competing viewpoints, acknowledging
    uncertainties and tensions.
    """

    def __init__(
        self,
        ensemble: Optional[PersonaEnsemble] = None,
        debate: Optional[InternalDebate] = None,
    ):
        """
        Initialize multi-persona agent.

        Args:
            ensemble: Custom persona ensemble (uses default if None)
            debate: Custom debate manager (uses default if None)
        """
        self.ensemble = ensemble or PersonaEnsemble()
        self.debate = debate or InternalDebate()
        self._llm_service: Optional["LLMService"] = None

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except ImportError:
                logger.warning("LLM service not available")
        return self._llm_service

    async def process(
        self,
        query: str,
        context: Optional[Dict] = None,
    ) -> MetaAgentResponse:
        """
        Process a query through multi-persona reasoning.

        Args:
            query: User query or topic to analyze
            context: Optional additional context

        Returns:
            MetaAgentResponse with synthesized answer and debate history
        """
        logger.info(f"Processing with {len(self.ensemble.personas)} personas")

        # Step 1: Generate perspectives
        perspectives = await self.ensemble.generate_perspectives(query, context)
        logger.info(f"Generated {len(perspectives)} perspectives")

        # Step 2: Run internal debate
        debate_result = await self.debate.run(perspectives, query)
        logger.info(
            f"Debate completed: {debate_result.total_rounds} rounds, "
            f"consensus: {debate_result.consensus_level.value}"
        )

        # Step 3: Synthesize final answer
        final_answer, rationale = await self._synthesize(
            query, perspectives, debate_result
        )

        # Calculate confidence based on consensus
        confidence = self._calculate_confidence(debate_result)

        return MetaAgentResponse(
            final_answer=final_answer,
            perspectives=perspectives,
            debate_result=debate_result,
            synthesis_rationale=rationale,
            confidence=confidence,
            metadata={
                "query": query,
                "persona_count": len(perspectives),
            },
        )

    def process_sync(
        self,
        query: str,
        context: Optional[Dict] = None,
    ) -> MetaAgentResponse:
        """Synchronous wrapper for process().

        WARNING: Must NOT be called from within an already-running event loop
        (e.g. inside FastAPI/uvicorn). Use ``await process()`` instead.
        """
        import asyncio

        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "process_sync() cannot be called from an async context. "
                "Use 'await process()' instead."
            )
        except RuntimeError as exc:
            if "no running event loop" not in str(exc):
                raise
        return asyncio.run(self.process(query, context))

    async def _synthesize(
        self,
        query: str,
        perspectives: List[Perspective],
        debate_result: DebateResult,
    ) -> tuple:
        """Synthesize final answer from debate."""
        if self.llm_service:
            return await self._synthesize_with_llm(query, perspectives, debate_result)
        return self._synthesize_simple(query, perspectives, debate_result)

    async def _synthesize_with_llm(
        self,
        query: str,
        perspectives: List[Perspective],
        debate_result: DebateResult,
    ) -> tuple:
        """Use LLM for sophisticated synthesis."""
        perspectives_text = "\n\n".join(
            f"**{p.persona_name}** ({p.role.value}):\n{p.content}" for p in perspectives
        )

        key_points = "\n".join(f"- {kp}" for kp in debate_result.key_points)
        tensions = "\n".join(f"- {t}" for t in debate_result.unresolved_tensions)

        prompt = f"""You are synthesizing multiple expert perspectives into a balanced response.

QUERY: {query}

PERSPECTIVES:
{perspectives_text}

KEY AGREEMENTS:
{key_points or "None identified"}

UNRESOLVED TENSIONS:
{tensions or "None"}

CONSENSUS LEVEL: {debate_result.consensus_level.value}

Provide:
1. A balanced synthesis that incorporates the strongest points from each perspective
2. Acknowledge uncertainties where consensus wasn't reached
3. Be direct and actionable

FORMAT:
ANSWER: [Your synthesized response]
RATIONALE: [Brief explanation of how you balanced the perspectives]"""

        try:
            response = await self.llm_service.generate_response(
                prompt, temperature=0.5, max_tokens=1000
            )

            # Parse response
            if "ANSWER:" in response:
                parts = response.split("RATIONALE:")
                answer = parts[0].replace("ANSWER:", "").strip()
                rationale = parts[1].strip() if len(parts) > 1 else ""
                return answer, rationale
            return response, "Direct synthesis"

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return self._synthesize_simple(query, perspectives, debate_result)

    def _synthesize_simple(
        self,
        query: str,
        perspectives: List[Perspective],
        debate_result: DebateResult,
    ) -> tuple:
        """Simple synthesis without LLM."""
        if not perspectives:
            return "Unable to generate perspectives.", "No perspectives available"

        # Use winning perspective as base
        if debate_result.winning_perspective:
            winner = next(
                (
                    p
                    for p in perspectives
                    if p.persona_name == debate_result.winning_perspective
                ),
                perspectives[0],
            )
            base = winner.content
        else:
            base = perspectives[0].content

        # Add key points
        if debate_result.key_points:
            additions = " Key considerations: " + "; ".join(debate_result.key_points)
        else:
            additions = ""

        # Acknowledge tensions
        if debate_result.unresolved_tensions:
            caveats = f" Note: {debate_result.unresolved_tensions[0]}"
        else:
            caveats = ""

        answer = base + additions + caveats
        rationale = f"Based on {len(perspectives)} perspectives with {debate_result.consensus_level.value} consensus"

        return answer, rationale

    def _calculate_confidence(self, debate_result: DebateResult) -> float:
        """Calculate confidence based on consensus level."""
        confidence_map = {
            ConsensusLevel.FULL: 0.95,
            ConsensusLevel.MAJORITY: 0.8,
            ConsensusLevel.PARTIAL: 0.6,
            ConsensusLevel.NONE: 0.4,
        }
        return confidence_map.get(debate_result.consensus_level, 0.5)

    def add_persona(self, persona, role=None):
        """Add a custom persona to the ensemble."""
        from .types import DebateRole

        self.ensemble.add_persona(persona, role or DebateRole.ADVOCATE)

    @property
    def persona_names(self) -> List[str]:
        """Get all persona names in the ensemble."""
        return self.ensemble.persona_names
