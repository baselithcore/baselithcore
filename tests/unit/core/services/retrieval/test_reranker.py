"""
Unit tests for Reranker service.
"""

import pytest
from unittest.mock import MagicMock, patch
from core.services.retrieval.reranker import Reranker, get_reranker
from core.models.domain import Document, SearchResult


@pytest.fixture
def mock_cross_encoder():
    with patch("core.services.retrieval.reranker.CrossEncoder") as m:
        yield m


def test_reranker_initialization(mock_cross_encoder):
    """Test reranker initialization."""
    r = Reranker()
    assert r._model is None
    assert r._enabled is True


def test_reranker_initialization_disabled():
    """Test reranker initialization when CrossEncoder is missing."""
    with patch("core.services.retrieval.reranker.CrossEncoder", None):
        r = Reranker()
        assert r._enabled is False


def test_reranker_lazy_loading(mock_cross_encoder):
    """Test lazy loading of CrossEncoder model."""
    r = Reranker()
    mock_inst = MagicMock()
    mock_cross_encoder.return_value = mock_inst

    # First access
    model = r.model
    assert model == mock_inst
    mock_cross_encoder.assert_called_once_with(r.model_name)

    # Second access
    model2 = r.model
    assert model2 == mock_inst
    mock_cross_encoder.assert_called_once()


def test_reranker_loading_error(mock_cross_encoder):
    """Test handling of error during model loading."""
    r = Reranker()
    mock_cross_encoder.side_effect = Exception("Load fail")

    model = r.model
    assert model is None
    assert r._enabled is False


def test_rerank_basic(mock_cross_encoder):
    """Test basic reranking logic."""
    r = Reranker()
    mock_inst = MagicMock()
    mock_inst.predict.return_value = [0.1, 0.9]
    mock_cross_encoder.return_value = mock_inst

    results = [
        SearchResult(document=Document(id="0", content="c0"), score=0.5),
        SearchResult(document=Document(id="1", content="c1"), score=0.5),
    ]

    reranked = r.rerank("q", results, top_k=2)
    assert len(reranked) == 2
    assert reranked[0].document.id == "1"
    assert reranked[0].score == 0.9
    assert reranked[1].document.id == "0"


def test_rerank_disabled_or_no_results(mock_cross_encoder):
    """Test rerank returns original when disabled or no results."""
    r = Reranker()
    r._enabled = False

    results = [SearchResult(document=Document(id="0", content="c0"), score=0.5)]
    assert r.rerank("q", results) == results

    r._enabled = True
    assert r.rerank("q", []) == []


def test_rerank_no_content(mock_cross_encoder):
    """Test rerank with documents having no content."""
    r = Reranker()
    mock_inst = MagicMock()
    mock_cross_encoder.return_value = mock_inst

    results = [SearchResult(document=Document(id="0", content=""), score=0.5)]
    assert r.rerank("q", results) == results
    mock_inst.predict.assert_not_called()


def test_rerank_exception_fallback(mock_cross_encoder):
    """Test fallback to original results on exception."""
    r = Reranker()
    mock_inst = MagicMock()
    mock_inst.predict.side_effect = Exception("Runtime fail")
    mock_cross_encoder.return_value = mock_inst

    results = [SearchResult(document=Document(id="0", content="c0"), score=0.5)]
    assert r.rerank("q", results) == results


def test_get_reranker_global():
    """Test global instance retrieval."""
    with patch("core.services.retrieval.reranker._reranker", None):
        inst = get_reranker()
        assert isinstance(inst, Reranker)
        inst2 = get_reranker()
        assert inst is inst2
