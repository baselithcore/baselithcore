import pytest
from unittest.mock import AsyncMock, Mock, patch, PropertyMock
from core.exploration.hypothesis import HypothesisGenerator, Hypothesis, ConfidenceLevel


@pytest.fixture
def mock_llm_service():
    """Mock LLM service."""
    service = Mock()
    service.generate_response = AsyncMock(
        return_value="""
HYPOTHESIS: Test hypothesis 1
ASSUMPTIONS: assumption1, assumption2
CONFIDENCE: high
---
HYPOTHESIS: Test hypothesis 2
ASSUMPTIONS: assumption3
CONFIDENCE: medium
---
"""
    )
    return service


@pytest.mark.asyncio
async def test_generator_initialization(mock_llm_service):
    """Test generator initialization."""
    generator = HypothesisGenerator(llm_service=mock_llm_service)
    assert generator.llm_service == mock_llm_service


@pytest.mark.asyncio
async def test_generate_with_llm(mock_llm_service):
    """Test hypothesis generation with LLM."""
    generator = HypothesisGenerator(llm_service=mock_llm_service)

    hypotheses = await generator.generate(
        context="test context", known_facts=["fact1"], unknowns=["unknown1"]
    )

    assert len(hypotheses) == 2
    assert isinstance(hypotheses[0], Hypothesis)
    assert hypotheses[0].statement == "Test hypothesis 1"
    assert hypotheses[0].confidence == ConfidenceLevel.HIGH
    assert "assumption1" in hypotheses[0].assumptions

    mock_llm_service.generate_response.assert_called_once()


@pytest.mark.asyncio
async def test_generate_simple_fallback():
    """Test fallback to simple generation when LLM is missing."""
    with patch(
        "core.exploration.hypothesis.HypothesisGenerator.llm_service",
        new_callable=PropertyMock,
    ) as mock_service_prop:
        mock_service_prop.return_value = None

        generator = HypothesisGenerator()

        hypotheses = await generator.generate(
            context="test context", unknowns=["unknown1", "unknown2"], max_hypotheses=2
        )

    assert len(hypotheses) == 2
    assert hypotheses[0].confidence == ConfidenceLevel.SPECULATIVE
    assert "unknown1" in hypotheses[0].statement


@pytest.mark.asyncio
async def test_generate_llm_failure(mock_llm_service):
    """Test fallback when LLM fails."""
    mock_llm_service.generate_response = AsyncMock(side_effect=Exception("LLM error"))
    generator = HypothesisGenerator(llm_service=mock_llm_service)

    hypotheses = await generator.generate(context="test context", unknowns=["unknown1"])

    # Should fall back to simple generation
    assert len(hypotheses) > 0
    assert hypotheses[0].confidence == ConfidenceLevel.SPECULATIVE


@pytest.mark.asyncio
async def test_parse_hypotheses_robustness(mock_llm_service):
    """Test parsing logic robustness."""
    # Malformed response
    mock_llm_service.generate_response = AsyncMock(
        return_value="""
Invalid format
---
HYPOTHESIS: valid one
ASSUMPTIONS: a1
CONFIDENCE: low
---
Another invalid one
"""
    )
    generator = HypothesisGenerator(llm_service=mock_llm_service)

    hypotheses = await generator.generate("context")

    assert len(hypotheses) == 1
    assert hypotheses[0].statement == "valid one"
