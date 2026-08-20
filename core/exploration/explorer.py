"""
Proactive Explorer

Enables agents to autonomously explore and gather information.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ExplorationResult:
    """
    Hypothesis-Driven Autonomous Exploration.

    Implements the 'Scientific Method' agentic pattern. Enables agents to
    systematically explore unknown domains by generating hypotheses,
    execoting experimental actions, and synthesizing new knowledge from
    empirical observations to expand the system's world model.
    """

    query: str
    findings: list[str]
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.5
    gaps_identified: list[str] = field(default_factory=list)


class KnowledgeSource(Protocol):
    """Protocol for knowledge sources to explore."""

    async def search(self, query: str) -> list[dict[str, Any]]:
        """Search the knowledge source."""
        ...

    async def get_related(self, topic: str) -> list[str]:
        """Get related topics."""
        ...


class ProactiveExplorer:
    """
    Proactively explores knowledge spaces to gather information.

    Features:
    - Query expansion for broader exploration
    - Knowledge gap identification
    - Source aggregation
    - Confidence scoring
    """

    def __init__(
        self,
        sources: list[KnowledgeSource] | None = None,
        llm_service=None,
    ):
        """
        Initialize explorer.

        Args:
            sources: List of KnowledgeSource implementations
            llm_service: Optional LLM for query expansion
        """
        self.sources = sources or []
        self._llm_service = llm_service

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

    async def explore(
        self,
        topic: str,
        depth: int = 1,
        max_results: int = 10,
    ) -> ExplorationResult:
        """
        Explore a topic across all sources.

        Args:
            topic: Topic to explore
            depth: How deep to explore related topics (default: 1)
            max_results: Maximum results to return

        Returns:
            ExplorationResult with findings
        """
        all_findings = []
        all_sources = []
        related_topics = set()

        # Expand query for better coverage
        queries = await self._expand_query(topic)

        async def _probe(source, query):
            """One independent (source, query) probe: search + related topics."""
            results = await source.search(query)
            related = ()
            if depth > 0:
                try:
                    related = await source.get_related(query)
                except Exception as e:
                    # Findings from the successful search are kept — same
                    # partial-progress behavior as the old serial loop.
                    logger.warning(f"Source exploration failed: {e}")
            return results, related

        # The source × query cross-product is independent network I/O:
        # bounded_gather overlaps the calls (results stay in submission order,
        # so aggregation below is byte-identical to the old serial loop) while
        # the limit keeps the fan-out from stampeding any single backend.
        from core.utils.concurrency import bounded_gather

        pairs = [(source, query) for source in self.sources for query in queries]
        outcomes = await bounded_gather(
            (_probe(source, query) for source, query in pairs),
            limit=8,
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                logger.warning(f"Source exploration failed: {outcome}")
                continue
            results, related = outcome
            for result in results[:max_results]:
                all_findings.append(str(result.get("content", result)))
                if "source" in result:
                    all_sources.append(result["source"])
            related_topics.update(related)

        # Identify knowledge gaps
        gaps = self._identify_gaps(topic, all_findings)

        # Calculate confidence based on findings
        confidence = min(1.0, len(all_findings) / max_results)

        return ExplorationResult(
            query=topic,
            findings=all_findings[:max_results],
            sources=list(set(all_sources)),
            confidence=confidence,
            gaps_identified=gaps,
        )

    async def _expand_query(self, topic: str) -> list[str]:
        """Expand topic into multiple search queries."""
        queries = [topic]

        # Simple expansions
        queries.append(f"what is {topic}")
        queries.append(f"{topic} overview")

        # LLM-based expansion if available
        if self.llm_service:
            try:
                prompt = f"""Generate 3 search queries to explore this topic: "{topic}"
                Return only the queries, one per line."""
                result = await self.llm_service.generate_response(prompt)
                queries.extend(result.strip().split("\n")[:3])
            except Exception as e:
                logger.debug(f"LLM query expansion failed: {e}")

        return queries

    def _identify_gaps(
        self,
        topic: str,
        findings: list[str],
    ) -> list[str]:
        """Identify knowledge gaps based on findings."""
        gaps = []

        # Simple gap detection
        if not findings:
            gaps.append(f"No information found about {topic}")
        elif len(findings) < 3:
            gaps.append(f"Limited information available for {topic}")

        return gaps

    def add_source(self, source: KnowledgeSource) -> None:
        """Add a knowledge source."""
        self.sources.append(source)
