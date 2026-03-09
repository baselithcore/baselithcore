"""
Unit tests for Document operations in the knowledge graph.
"""

import pytest
from unittest.mock import MagicMock
from core.services.graph.ops.document_ops import DocumentOperations


@pytest.fixture
def mock_graph_client():
    client = MagicMock()
    client.is_enabled.return_value = True
    return client


@pytest.fixture
def doc_ops(mock_graph_client):
    return DocumentOperations(mock_graph_client)


def test_upsert_document_basic(doc_ops, mock_graph_client):
    """Test basic document upsert."""
    doc_ops.upsert_document("doc1", "pdf", "/path/to/doc.pdf")

    mock_graph_client.upsert_node.assert_called_once()
    args, kwargs = mock_graph_client.upsert_node.call_args
    assert args[0] == "doc1"
    assert "Document" in kwargs["labels"]
    assert kwargs["properties"]["type"] == "pdf"
    assert kwargs["properties"]["name"] == "doc.pdf"


def test_upsert_document_with_category_kb(doc_ops, mock_graph_client):
    """Test upsert with KnowledgeBase category (triggers label removal)."""
    doc_ops.upsert_document("doc1", "text", "/path/doc.txt", category="knowledge-base")

    # Check upsert
    assert "KnowledgeBase" in mock_graph_client.upsert_node.call_args[1]["labels"]

    # Check Analysis label removal
    mock_graph_client.query.assert_called_with(
        "MATCH (n {id: $id}) REMOVE n:Analysis", {"id": "doc1"}
    )


def test_upsert_document_high_risk(doc_ops, mock_graph_client):
    """Test upsert with high risk properties."""
    doc_ops.upsert_document(
        "doc1", "text", "/path/doc.txt", properties={"risk_level": "High"}
    )

    labels = mock_graph_client.upsert_node.call_args[1]["labels"]
    props = mock_graph_client.upsert_node.call_args[1]["properties"]
    assert "HighRisk" in labels
    assert "⚠️" in props["label_display"]


def test_upsert_document_medium_risk(doc_ops, mock_graph_client):
    """Test upsert with medium risk properties."""
    doc_ops.upsert_document(
        "doc1", "text", "/path/doc.txt", properties={"risk_level": "Medium"}
    )

    labels = mock_graph_client.upsert_node.call_args[1]["labels"]
    assert "MediumRisk" in labels


def test_upsert_document_disabled_graph(doc_ops, mock_graph_client):
    """Test upsert returns early when graph is disabled."""
    mock_graph_client.is_enabled.return_value = False
    doc_ops.upsert_document("doc1", "pdf", "/path/doc.pdf")
    mock_graph_client.upsert_node.assert_not_called()


def test_upsert_document_error_handling(doc_ops, mock_graph_client):
    """Test upsert handles exceptions gracefully."""
    mock_graph_client.upsert_node.side_effect = Exception("Boom")
    # Should not raise
    doc_ops.upsert_document("doc1", "pdf", "/path/doc.pdf")


def test_transition_document_to_kb_success(doc_ops, mock_graph_client):
    """Test successful transition to KnowledgeBase."""
    doc_ops.transition_document_to_kb("doc1", new_path="/new/path")

    mock_graph_client.query.assert_called_once()
    cypher = mock_graph_client.query.call_args[0][0]
    params = mock_graph_client.query.call_args[0][1]

    assert "MATCH (n {id: $id})" in cypher
    assert "REMOVE n:Analysis" in cypher
    assert "SET n:KnowledgeBase" in cypher
    assert params["id"] == "doc1"
    assert params["path"] == "/new/path"


def test_transition_document_to_kb_disabled(doc_ops, mock_graph_client):
    """Test transition returns early when disabled."""
    mock_graph_client.is_enabled.return_value = False
    doc_ops.transition_document_to_kb("doc1")
    mock_graph_client.query.assert_not_called()


def test_transition_document_to_kb_error(doc_ops, mock_graph_client):
    """Test transition handles exceptions."""
    mock_graph_client.query.side_effect = Exception("Cypher fail")
    # Should not raise
    doc_ops.transition_document_to_kb("doc1")
