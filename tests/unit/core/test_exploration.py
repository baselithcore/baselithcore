"""
Unit Tests for Core Exploration Module

Tests for proactive exploration and hypothesis generation.
"""

import pytest
from unittest.mock import patch, PropertyMock
from core.exploration import (
    ProactiveExplorer,
    ExplorationResult,
    HypothesisGenerator,
    Hypothesis,
)
from core.exploration.hypothesis import ConfidenceLevel


# ============================================================================
# Mock Knowledge Source
# ============================================================================


class MockKnowledgeSource:
    """Mock knowledge source for testing."""

    def __init__(self, results=None, related=None):
        self.results = results or []
        self.related = related or []
        self.search_called = 0

    async def search(self, query):
        self.search_called += 1
        return self.results

    async def get_related(self, topic):
        return self.related


# ============================================================================
# ExplorationResult Tests
# ============================================================================


class TestExplorationResult:
    """Tests for ExplorationResult dataclass."""

    def test_creation_with_defaults(self):
        """Create result with minimal fields."""
        result = ExplorationResult(query="test", findings=["finding1"])

        assert result.query == "test"
        assert result.findings == ["finding1"]
        assert result.confidence == 0.5
        assert result.gaps_identified == []

    def test_creation_with_all_fields(self):
        """Create result with all fields."""
        result = ExplorationResult(
            query="test query",
            findings=["f1", "f2"],
            sources=["src1"],
            confidence=0.8,
            gaps_identified=["gap1"],
        )

        assert result.confidence == 0.8
        assert "gap1" in result.gaps_identified


# ============================================================================
# ProactiveExplorer Tests
# ============================================================================


class TestProactiveExplorer:
    """Tests for ProactiveExplorer."""

    def test_initialization(self):
        """Basic initialization."""
        explorer = ProactiveExplorer()

        assert explorer.sources == []

    def test_add_source(self):
        """Add knowledge source."""
        explorer = ProactiveExplorer()
        source = MockKnowledgeSource()

        explorer.add_source(source)

        assert len(explorer.sources) == 1

    @pytest.mark.asyncio
    async def test_explore_no_sources(self):
        """Explore with no sources."""
        explorer = ProactiveExplorer()

        result = await explorer.explore("test topic")

        assert result.query == "test topic"
        assert result.findings == []
        assert "No information found" in result.gaps_identified[0]

    @pytest.mark.asyncio
    async def test_explore_with_source(self):
        """Explore with mock source."""
        source = MockKnowledgeSource(
            results=[{"content": "finding1"}, {"content": "finding2"}]
        )
        explorer = ProactiveExplorer(sources=[source])

        result = await explorer.explore("test topic")

        assert len(result.findings) > 0
        assert source.search_called > 0

    @pytest.mark.asyncio
    async def test_explore_confidence_calculation(self):
        """Confidence based on findings count."""
        results = [{"content": f"finding{i}"} for i in range(10)]
        source = MockKnowledgeSource(results=results)
        explorer = ProactiveExplorer(sources=[source])

        result = await explorer.explore("topic", max_results=10)

        # Full results = higher confidence
        assert result.confidence > 0.5


# ============================================================================
# Hypothesis Tests
# ============================================================================


class TestHypothesis:
    """Tests for Hypothesis dataclass."""

    def test_creation(self):
        """Basic hypothesis creation."""
        hyp = Hypothesis(
            statement="Test hypothesis",
            confidence=ConfidenceLevel.MEDIUM,
        )

        assert hyp.statement == "Test hypothesis"
        assert hyp.confidence == ConfidenceLevel.MEDIUM

    def test_is_testable_with_assumptions(self):
        """Hypothesis with assumptions is testable."""
        hyp = Hypothesis(
            statement="Test",
            confidence=ConfidenceLevel.HIGH,
            assumptions=["assumption1"],
        )

        assert hyp.is_testable is True

    def test_is_testable_without_assumptions(self):
        """Hypothesis without assumptions is not testable."""
        hyp = Hypothesis(
            statement="Test",
            confidence=ConfidenceLevel.HIGH,
        )

        assert hyp.is_testable is False


# ============================================================================
# HypothesisGenerator Tests
# ============================================================================


class TestHypothesisGenerator:
    """Tests for HypothesisGenerator."""

    def test_initialization(self):
        """Basic initialization."""
        gen = HypothesisGenerator()

        assert gen._llm_service is None

    @pytest.mark.asyncio
    async def test_generate_simple_no_llm(self):
        """Generate hypotheses without LLM."""
        with patch(
            "core.exploration.hypothesis.HypothesisGenerator.llm_service",
            new_callable=PropertyMock,
        ) as mock_prop:
            mock_prop.return_value = None
            gen = HypothesisGenerator()

            hypotheses = await gen.generate(
                context="test context",
                unknowns=["unknown1", "unknown2"],
            )

            assert len(hypotheses) > 0
            assert all(isinstance(h, Hypothesis) for h in hypotheses)

    @pytest.mark.asyncio
    async def test_generate_with_gaps(self):
        """Generate hypotheses from gaps."""
        gen = HypothesisGenerator()

        hypotheses = await gen.generate(
            context="data analysis",
            unknowns=["missing data", "unclear pattern"],
            max_hypotheses=2,
        )

        assert len(hypotheses) <= 2

    @pytest.mark.asyncio
    async def test_generate_fallback_empty_unknowns(self):
        """Generate fallback when no unknown."""
        with patch(
            "core.exploration.hypothesis.HypothesisGenerator.llm_service",
            new_callable=PropertyMock,
        ) as mock_prop:
            mock_prop.return_value = None
            gen = HypothesisGenerator()

            hypotheses = await gen.generate(
                context="test",
                max_hypotheses=3,
            )

            assert len(hypotheses) >= 1


# ============================================================================
# Integration Test
# ============================================================================


@pytest.mark.asyncio
async def test_exploration_to_hypothesis_flow():
    """Full flow: explore -> identify gaps -> generate hypotheses."""
    # Setup explorer with limited results
    source = MockKnowledgeSource(results=[{"content": "partial info"}])

    with patch(
        "core.exploration.explorer.ProactiveExplorer.llm_service",
        new_callable=PropertyMock,
    ) as mock_exp_llm:
        mock_exp_llm.return_value = None
        explorer = ProactiveExplorer(sources=[source])

        # Explore
        exploration = await explorer.explore("complex topic")

    # Should have findings
    assert len(exploration.findings) > 0

    # Confidence should be low due to limited results
    assert exploration.confidence < 1.0

    # Generate hypotheses about the topic - patch LLM for stability in tests
    with patch(
        "core.exploration.hypothesis.HypothesisGenerator.llm_service",
        new_callable=PropertyMock,
    ) as mock_hyp_llm:
        mock_hyp_llm.return_value = None
        gen = HypothesisGenerator()
        hypotheses = await gen.generate(
            context="complex topic",
            unknowns=["need more data"],  # Provide unknowns explicitly
        )

    assert len(hypotheses) > 0
