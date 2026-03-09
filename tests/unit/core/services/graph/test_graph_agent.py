"""
Tests for core/services/graph/agent.py

Tests GraphService for graph operations.
"""

from unittest.mock import MagicMock


class TestGraphServiceInit:
    """Tests for GraphService initialization."""

    def test_init_with_graph_client(self):
        """Verify GraphService initializes with graph client."""
        mock_client = MagicMock()

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)

        assert service.client is mock_client
        assert service._doc_ops is not None


class TestGraphServiceUpsertDocument:
    """Tests for GraphService.upsert_document method."""

    def test_upsert_document_delegates_to_doc_ops(self):
        """Verify upsert_document delegates to DocumentOperations."""
        mock_client = MagicMock()

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service._doc_ops = MagicMock()

        service.upsert_document(
            doc_id="doc_1",
            doc_type="Markdown",
            path="/docs/test.md",
            category="Technical",
            properties={"size": 1024},
        )

        service._doc_ops.upsert_document.assert_called_once_with(
            "doc_1", "Markdown", "/docs/test.md", "Technical", {"size": 1024}
        )


class TestGraphServiceTransitionDocument:
    """Tests for GraphService.transition_document_to_kb method."""

    def test_transition_document_delegates_to_doc_ops(self):
        """Verify transition_document_to_kb delegates to DocumentOperations."""
        mock_client = MagicMock()

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service._doc_ops = MagicMock()

        service.transition_document_to_kb("doc_1", new_path="/kb/doc.md")

        service._doc_ops.transition_document_to_kb.assert_called_once_with(
            "doc_1", "/kb/doc.md"
        )


class TestGraphServiceLinkEntities:
    """Tests for GraphService.link_entities method."""

    def test_link_entities_creates_edge(self):
        """Verify link_entities creates edge via client."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service.link_entities("entity_1", "RELATED_TO", "entity_2", {"weight": 0.8})

        mock_client.upsert_edge.assert_called_once_with(
            "entity_1", "RELATED_TO", "entity_2", {"weight": 0.8}
        )

    def test_link_entities_returns_early_when_disabled(self):
        """Verify link_entities returns early when graph is disabled."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = False

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service.link_entities("entity_1", "RELATED_TO", "entity_2")

        mock_client.upsert_edge.assert_not_called()

    def test_link_entities_handles_exception(self):
        """Verify link_entities handles exceptions gracefully."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True
        mock_client.upsert_edge.side_effect = Exception("Graph error")

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        # Should not raise
        service.link_entities("entity_1", "RELATED_TO", "entity_2")


class TestGraphServiceRegisterRagUsage:
    """Tests for GraphService.register_rag_usage method."""

    def test_register_rag_usage_creates_session_and_edges(self):
        """Verify register_rag_usage creates session node and edges."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        # Remove doc_ops method to test fallback
        service._doc_ops = MagicMock(spec=[])

        doc_sources = [
            {"document_id": "doc_1"},
            {"id": "doc_2"},
            {},  # Empty source - should be skipped
        ]

        service.register_rag_usage("session_123", doc_sources)

        # Session node should be created
        mock_client.upsert_node.assert_called_once()
        call_args = mock_client.upsert_node.call_args
        assert call_args[0][0] == "session_123"
        assert "Session" in call_args[1]["labels"]

        # Two edges should be created (one for each valid doc)
        assert mock_client.upsert_edge.call_count == 2

    def test_register_rag_usage_returns_early_when_disabled(self):
        """Verify register_rag_usage returns early when graph is disabled."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = False

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service._doc_ops = MagicMock(spec=[])

        service.register_rag_usage("session_123", [{"document_id": "doc_1"}])

        mock_client.upsert_node.assert_not_called()

    def test_register_rag_usage_delegates_to_doc_ops_if_available(self):
        """Verify register_rag_usage delegates to doc_ops if method exists."""
        mock_client = MagicMock()

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service._doc_ops = MagicMock()
        service._doc_ops.register_rag_usage = MagicMock()

        doc_sources = [{"document_id": "doc_1"}]
        service.register_rag_usage("session_123", doc_sources)

        service._doc_ops.register_rag_usage.assert_called_once_with(
            "session_123", doc_sources
        )

    def test_register_rag_usage_handles_exception(self):
        """Verify register_rag_usage handles exceptions gracefully."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True
        mock_client.upsert_node.side_effect = Exception("Graph error")

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        service._doc_ops = MagicMock(spec=[])

        # Should not raise
        service.register_rag_usage("session_123", [{"document_id": "doc_1"}])


class TestGraphServiceReason:
    """Tests for GraphService.reason method."""

    def test_reason_returns_disabled_status_when_graph_disabled(self):
        """Verify reason returns disabled status when graph is off."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = False

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        result = service.reason("explain", ["entity_1"])

        assert result["status"] == "disabled"
        assert "reasoning_context" in result

    def test_reason_returns_empty_context_for_no_entities(self):
        """Verify reason returns empty context when no entities provided."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        result = service.reason("explain", [])

        assert result["status"] == "success"
        assert "No entities provided" in result["reasoning_context"]

    def test_reason_queries_graph_and_returns_context(self):
        """Verify reason queries graph and formats results."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True
        mock_client.query.return_value = [
            ["entity_1", "Summary 1", "RELATED_TO", "Summary 2"],
            ["entity_1", "Summary 1", "DEPENDS_ON", "Summary 3"],
        ]

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        result = service.reason("explain", ["entity_1"])

        assert result["status"] == "success"
        assert "Knowledge Context" in result["reasoning_context"]
        mock_client.query.assert_called_once()

    def test_reason_handles_empty_results(self):
        """Verify reason handles empty query results."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True
        mock_client.query.return_value = []

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        result = service.reason("explain", ["entity_1"])

        assert result["status"] == "success"
        assert "No specific graph context found" in result["reasoning_context"]

    def test_reason_handles_query_exception(self):
        """Verify reason handles query exceptions gracefully."""
        mock_client = MagicMock()
        mock_client.is_enabled.return_value = True
        mock_client.query.side_effect = Exception("Query error")

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        result = service.reason("explain", ["entity_1"])

        # Should return success with empty context rather than failing
        assert result["status"] == "success"


class TestGraphServiceCurrentTimestamp:
    """Tests for GraphService._current_timestamp method."""

    def test_current_timestamp_returns_string(self):
        """Verify _current_timestamp returns ISO format string."""
        mock_client = MagicMock()

        from core.services.graph.agent import GraphService

        service = GraphService(mock_client)
        timestamp = service._current_timestamp()

        assert isinstance(timestamp, str)
        assert len(timestamp) > 0
