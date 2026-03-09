"""
Research Assistant - Scientific paper analysis example.

Demonstrates:
- Paper metadata extraction
- Citation graph building
- Semantic search across papers
- Multi-document synthesis
"""

from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional
from datetime import datetime

from core.lifecycle import LifecycleMixin, AgentState, AgentError, FrameworkErrorCode
from core.orchestration.protocols import AgentProtocol
from pydantic import BaseModel

logger = get_logger(__name__)

# ============================================================================
# Models
# ============================================================================


class Paper(BaseModel):
    """Scientific paper model."""

    id: str
    title: str
    authors: List[str] = []
    abstract: str = ""
    keywords: List[str] = []
    year: Optional[int] = None
    citations: List[str] = []  # IDs of cited papers


class SearchResult(BaseModel):
    """Search result item."""

    paper_id: str
    title: str
    score: float
    snippet: str


class SynthesisRequest(BaseModel):
    """Multi-paper synthesis request."""

    paper_ids: List[str]
    topic: str


class SynthesisResult(BaseModel):
    """Synthesis result."""

    summary: str
    common_themes: List[str]
    key_findings: List[str]


# ============================================================================
# Research Assistant Agent
# ============================================================================


class ResearchAssistantAgent(LifecycleMixin, AgentProtocol):
    """Research assistant agent with paper analysis and citation tracking."""

    def __init__(self):
        super().__init__()
        self.papers: Dict[str, Paper] = {}

    async def _do_startup(self) -> None:
        """Initialize resources and load sample data."""
        logger.info("[ResearchAssistant] Initializing paper database...")
        self._load_sample_data()
        logger.info(f"[ResearchAssistant] Loaded {len(self.papers)} sample papers.")

    async def _do_shutdown(self) -> None:
        """Cleanup resources."""
        logger.info("[ResearchAssistant] Shutting down...")
        self.papers.clear()

    def _load_sample_data(self):
        """Load sample papers for demo."""
        self.papers["p1"] = Paper(
            id="p1",
            title="Attention Is All You Need",
            authors=["Vaswani et al."],
            abstract="The dominant sequence transduction models...",
            keywords=["transformer", "attention", "NLP"],
            year=2017,
            citations=[],
        )
        self.papers["p2"] = Paper(
            id="p2",
            title="BERT: Pre-training of Deep Bidirectional Transformers",
            authors=["Devlin et al."],
            abstract="We introduce a new language representation model...",
            keywords=["BERT", "pre-training", "NLP"],
            year=2018,
            citations=["p1"],
        )

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> Any:
        """
        Agent execution entry point.
        Dispatches to internal methods based on input/context.
        """
        if self.state != AgentState.READY:
            raise AgentError(
                "Agent not ready", code=FrameworkErrorCode.AGENT_NOT_READY
            )

        # Basic command dispatching for demo purposes
        cmd = input.lower().strip()
        if "search" in cmd:
            query = context.get("query", cmd.replace("search", "").strip()) if context else cmd.replace("search", "").strip()
            return await self.search(query)
        elif "synthesize" in cmd:
            paper_ids = context.get("paper_ids", []) if context else []
            topic = context.get("topic", "general") if context else "general"
            return await self.synthesize(paper_ids, topic)
        
        return {"status": "error", "message": f"Unknown command: {cmd}"}

    async def add_paper(self, content: str, filename: str) -> Paper:
        """Add paper from content (simplified extraction)."""
        paper_id = f"p{len(self.papers) + 1}"

        # Simple extraction
        lines = content.split("\n")
        title = lines[0] if lines else filename

        paper = Paper(
            id=paper_id,
            title=title[:100],
            authors=["Unknown Author"],
            abstract=content[:500] if len(content) > 100 else content,
            keywords=[],
            year=datetime.now().year,
        )

        self.papers[paper_id] = paper
        return paper

    async def search(self, query: str, limit: int = 10) -> List[SearchResult]:
        """Search papers by keyword matching."""
        results = []
        query_lower = query.lower()

        for paper in self.papers.values():
            text = f"{paper.title} {paper.abstract} {' '.join(paper.keywords)}"
            if query_lower in text.lower():
                results.append(
                    SearchResult(
                        paper_id=paper.id,
                        title=paper.title,
                        score=1.0,
                        snippet=paper.abstract[:200],
                    )
                )

        return results[:limit]

    async def get_citations(self, paper_id: str) -> Dict[str, List[Dict[str, Any]]]:
        """Get citation graph for a paper."""
        if paper_id not in self.papers:
            return {"nodes": [], "edges": []}

        paper = self.papers[paper_id]
        nodes = [{"id": paper_id, "title": paper.title}]
        edges = []

        for cited_id in paper.citations:
            if cited_id in self.papers:
                nodes.append({"id": cited_id, "title": self.papers[cited_id].title})
                edges.append(
                    {"source": paper_id, "target": cited_id, "relation": "CITES"}
                )

        return {"nodes": nodes, "edges": edges}

    async def synthesize(self, paper_ids: List[str], topic: str) -> SynthesisResult:
        """Generate synthesis from multiple papers."""
        papers = [self.papers[pid] for pid in paper_ids if pid in self.papers]

        if not papers:
            return SynthesisResult(
                summary="No papers found.", common_themes=[], key_findings=[]
            )

        all_keywords = []
        for p in papers:
            all_keywords.extend(p.keywords)

        common = list(set(all_keywords))[:5]
        years = [p.year for p in papers if p.year is not None]

        return SynthesisResult(
            summary=f"Synthesis of {len(papers)} papers on '{topic}':\n"
            + "\n".join(f"- {p.title}" for p in papers),
            common_themes=common,
            key_findings=[
                f"Papers span {min(years)} to {max(years)}" if years else "Unknown timeframe"
            ],
        )
