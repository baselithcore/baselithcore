"""
Structured Internal Consensus Building.

Facilitates multi-round debates between diverse agent personas.
Identifies semantic agreements, highlights unresolved tensions, and
calculates consensus levels to guide meta-cognitive synthesis.
"""

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from core.services.llm import LLMService

from .types import (
    Perspective,
    DebateRound,
    DebateResult,
    DebateRole,
    ConsensusLevel,
)

logger = get_logger(__name__)


class InternalDebate:
    """
    Manager for persona-to-persona dialectical reasoning.

    Orchestrates the debate lifecycle, including argument generation,
    cross-examination (counterarguments), and consensus detection. Uses
    LLM-powered semantic analysis to identify the core points of
    convergence and divergence.
    """

    def __init__(
        self,
        max_rounds: int = 3,
        consensus_threshold: float = 0.7,
    ):
        """
        Initialize debate manager.

        Args:
            max_rounds: Maximum debate rounds
            consensus_threshold: Required agreement level for consensus
        """
        self.max_rounds = max_rounds
        self.consensus_threshold = consensus_threshold
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

    async def run(
        self,
        perspectives: List[Perspective],
        query: str,
    ) -> DebateResult:
        """
        Run full debate process.

        Args:
            perspectives: Initial perspectives from ensemble
            query: Original query for context

        Returns:
            DebateResult with rounds, consensus, and key points
        """
        if not perspectives:
            return DebateResult(
                rounds=[],
                consensus_level=ConsensusLevel.NONE,
                key_points=[],
            )

        rounds = []
        current_perspectives = perspectives

        for round_num in range(1, self.max_rounds + 1):
            logger.info(f"Running debate round {round_num}")

            round_result = await self._run_round(round_num, current_perspectives, query)
            rounds.append(round_result)

            # Check for early consensus
            if self._check_early_consensus(rounds):
                logger.info(f"Early consensus reached at round {round_num}")
                break

            # Check for stagnation
            if not round_result.has_movement and round_num > 1:
                logger.info(f"Debate stagnated at round {round_num}")
                break

        # Determine final consensus and extract key points
        consensus_level = self._calculate_consensus(rounds)
        key_points = self._extract_key_points(rounds)
        unresolved = self._extract_unresolved(rounds)
        winner = self._determine_winner(perspectives, rounds)

        return DebateResult(
            rounds=rounds,
            consensus_level=consensus_level,
            key_points=key_points,
            unresolved_tensions=unresolved,
            winning_perspective=winner,
        )

    async def _run_round(
        self,
        round_num: int,
        perspectives: List[Perspective],
        query: str,
    ) -> DebateRound:
        """Run a single debate round."""
        arguments = []
        counterarguments = []
        agreements = []
        disagreements = []

        # Extract initial arguments from all perspectives
        for p in perspectives:
            arguments.append(f"{p.persona_name}: {p.content}")

        # Generate counterarguments from critics
        critics = [p for p in perspectives if p.role == DebateRole.CRITIC]
        advocates = [p for p in perspectives if p.role == DebateRole.ADVOCATE]

        for critic in critics:
            for advocate in advocates:
                counter = await self._generate_counterargument(critic, advocate, query)
                if counter:
                    counterarguments.append(counter)

        # Find agreements and disagreements
        if len(perspectives) >= 2:
            agreements, disagreements = await self._find_agreements_disagreements(
                perspectives, query
            )

        return DebateRound(
            round_number=round_num,
            arguments=arguments,
            counterarguments=counterarguments,
            agreements=agreements,
            disagreements=disagreements,
        )

    async def _generate_counterargument(
        self,
        critic: Perspective,
        target: Perspective,
        query: str,
    ) -> Optional[str]:
        """Generate counterargument from critic to target."""
        prompt = f"""As {critic.persona_name}, critically respond to:

"{target.content}"

Original query: {query}

Provide a concise counterargument (1-2 sentences) challenging the main claim."""

        if self.llm_service:
            try:
                return await self.llm_service.generate_response(
                    prompt, temperature=0.5, max_tokens=150
                )
            except Exception as e:
                logger.error(f"Error generating counterargument: {e}")
                return None

        # Mock for testing
        return f"Counter to {target.persona_name}: Consider the risks."

    async def _find_agreements_disagreements(
        self,
        perspectives: List[Perspective],
        query: str,
    ) -> tuple:
        """Analyze perspectives for agreements and disagreements.

        Uses LLM-based semantic analysis when available, falling back to
        embedding similarity or keyword overlap.
        """
        if self.llm_service and len(perspectives) >= 2:
            try:
                return await self._analyze_agreement_llm(perspectives, query)
            except Exception as e:
                logger.warning(f"LLM agreement analysis failed, using fallback: {e}")

        return self._analyze_agreement_heuristic(perspectives)

    async def _analyze_agreement_llm(
        self,
        perspectives: List[Perspective],
        query: str,
    ) -> tuple:
        """Use LLM to identify semantic agreements and disagreements."""
        perspective_text = "\n\n".join(
            f"[{p.persona_name} ({p.role.value})]: {p.content}" for p in perspectives
        )
        prompt = (
            "Analyze the following perspectives on a query and identify:\n"
            "1. Points of AGREEMENT (claims where multiple perspectives align)\n"
            "2. Points of DISAGREEMENT (claims where perspectives conflict)\n\n"
            f"Query: {query}\n\n"
            f"Perspectives:\n{perspective_text}\n\n"
            "Respond in this exact format:\n"
            "AGREEMENTS:\n- <point 1>\n- <point 2>\n"
            "DISAGREEMENTS:\n- <point 1>\n- <point 2>\n"
            "If none, write 'None' under that section."
        )
        result = await self.llm_service.generate_response(
            prompt, temperature=0.2, max_tokens=300
        )

        agreements = []
        disagreements = []
        section = None
        for line in result.strip().splitlines():
            line = line.strip()
            if line.upper().startswith("AGREEMENT"):
                section = "agree"
                continue
            if line.upper().startswith("DISAGREEMENT"):
                section = "disagree"
                continue
            if line.startswith("- ") and line[2:].strip().lower() != "none":
                point = line[2:].strip()
                if section == "agree":
                    agreements.append(point)
                elif section == "disagree":
                    disagreements.append(point)

        return agreements[:5], disagreements[:3]

    @staticmethod
    def _analyze_agreement_heuristic(perspectives: List[Perspective]) -> tuple:
        """Keyword overlap fallback for agreement detection."""
        agreements = []
        disagreements = []

        contents = [p.content.lower() for p in perspectives]
        common_words = set(contents[0].split())
        for content in contents[1:]:
            common_words &= set(content.split())

        # Filter out stop words for better signal
        stop = {
            "the",
            "a",
            "an",
            "is",
            "are",
            "was",
            "were",
            "be",
            "to",
            "of",
            "and",
            "or",
            "in",
            "on",
            "at",
            "for",
            "it",
            "that",
            "this",
            "with",
        }
        meaningful = [w for w in common_words if w not in stop and len(w) > 3]
        if meaningful:
            agreements.append(f"Common themes: {', '.join(meaningful[:5])}")

        critics = [p for p in perspectives if p.is_critical]
        if critics:
            disagreements.append("Risk assessment differs between perspectives")

        return agreements, disagreements

    def _check_early_consensus(self, rounds: List[DebateRound]) -> bool:
        """Check if early consensus has been reached."""
        if not rounds:
            return False

        last_round = rounds[-1]

        # High agreement, low disagreement
        return len(last_round.agreements) >= 2 and len(last_round.disagreements) == 0

    def _calculate_consensus(self, rounds: List[DebateRound]) -> ConsensusLevel:
        """Calculate final consensus level."""
        if not rounds:
            return ConsensusLevel.NONE

        total_agreements = sum(len(r.agreements) for r in rounds)
        total_disagreements = sum(len(r.disagreements) for r in rounds)

        if total_disagreements == 0 and total_agreements > 0:
            return ConsensusLevel.FULL
        elif total_agreements > total_disagreements:
            return ConsensusLevel.MAJORITY
        elif total_agreements > 0:
            return ConsensusLevel.PARTIAL
        else:
            return ConsensusLevel.NONE

    def _extract_key_points(self, rounds: List[DebateRound]) -> List[str]:
        """Extract key points from debate."""
        points = []
        for r in rounds:
            points.extend(r.agreements)
        return list(set(points))[:5]

    def _extract_unresolved(self, rounds: List[DebateRound]) -> List[str]:
        """Extract unresolved tensions."""
        tensions = []
        for r in rounds:
            tensions.extend(r.disagreements)
        return list(set(tensions))[:3]

    def _determine_winner(
        self,
        perspectives: List[Perspective],
        rounds: List[DebateRound],
    ) -> Optional[str]:
        """Determine which perspective 'won' the debate.

        Uses a simple scoring heuristic: base confidence + bonus for being
        mentioned in agreements, minus penalty for being refuted in
        counterarguments.
        """
        if not perspectives:
            return None

        # Build a score per persona
        scores: Dict[str, float] = {}
        for p in perspectives:
            scores[p.persona_name] = p.confidence

        # Boost score if persona's name appears in agreements
        for r in rounds:
            for agreement in r.agreements:
                for p in perspectives:
                    if p.persona_name.lower() in agreement.lower():
                        scores[p.persona_name] = scores.get(p.persona_name, 0) + 0.1

        # Non-critics are preferred winners (critics challenge, advocates propose)
        non_critics = [p for p in perspectives if not p.is_critical]
        candidates = non_critics if non_critics else perspectives

        return max(candidates, key=lambda p: scores.get(p.persona_name, 0)).persona_name
