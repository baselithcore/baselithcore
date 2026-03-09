"""
Tests for core/graph/linking.py

Tests generic graph linking functions.
"""

from unittest.mock import MagicMock


class TestLinkNodeToExternalIssue:
    """Tests for link_node_to_external_issue function."""

    def test_link_node_to_issue_basic(self):
        """Verify basic node-to-issue linking."""
        from core.graph.linking import link_node_to_external_issue

        mock_upsert_node = MagicMock()
        mock_upsert_edge = MagicMock()

        link_node_to_external_issue(
            mock_upsert_node, mock_upsert_edge, source_id="story_1", issue_key="EXT-123"
        )

        # ExternalIssue node should be created
        mock_upsert_node.assert_called_once()
        call_args = mock_upsert_node.call_args
        assert call_args[0][0] == "EXT-123"
        assert "ExternalIssue" in call_args[1]["labels"]

        # Edge should be created
        mock_upsert_edge.assert_called_once_with("story_1", "LINKED_ISSUE", "EXT-123")

    def test_link_node_to_issue_no_key_returns_early(self):
        """Verify early return when no issue_key provided."""
        from core.graph.linking import link_node_to_external_issue

        mock_upsert_node = MagicMock()
        mock_upsert_edge = MagicMock()

        link_node_to_external_issue(
            mock_upsert_node, mock_upsert_edge, source_id="story_1"
        )

        mock_upsert_node.assert_not_called()
        mock_upsert_edge.assert_not_called()

    def test_link_node_to_issue_with_properties(self):
        """Verify issue linking with properties."""
        from core.graph.linking import link_node_to_external_issue

        mock_upsert_node = MagicMock()
        mock_upsert_edge = MagicMock()

        link_node_to_external_issue(
            mock_upsert_node,
            mock_upsert_edge,
            source_id="story_1",
            issue_key="EXT-123",
            issue_status="Open",
            issue_url="https://tracker.example.com/EXT-123",
            issue_source="GenericTracker",
        )

        call_args = mock_upsert_node.call_args
        props = call_args[1]["properties"]
        assert props["status"] == "Open"
        assert props["url"] == "https://tracker.example.com/EXT-123"
        assert props["source"] == "GenericTracker"


class TestGetLinkedExternalIssues:
    """Tests for get_linked_external_issues function."""

    def test_get_linked_issues_returns_list(self):
        """Verify get_linked_external_issues returns a list."""
        from core.graph.linking import get_linked_external_issues

        mock_query = MagicMock(return_value=[])

        result = get_linked_external_issues(mock_query, "doc_1")

        assert isinstance(result, list)

    def test_get_linked_issues_parses_results(self):
        """Verify correct parsing of query results."""
        from core.graph.linking import get_linked_external_issues

        mock_query = MagicMock(
            return_value=[["EXT-123", "Open", "https://url.com", "GenericTracker"]]
        )

        result = get_linked_external_issues(mock_query, "doc_1")

        assert len(result) == 1
        assert result[0]["key"] == "EXT-123"
        assert result[0]["status"] == "Open"
        assert result[0]["url"] == "https://url.com"
        assert result[0]["source"] == "GenericTracker"

    def test_get_linked_issues_handles_none_results(self):
        """Verify handling of None query results."""
        from core.graph.linking import get_linked_external_issues

        mock_query = MagicMock(return_value=None)

        result = get_linked_external_issues(mock_query, "doc_1")

        assert result == []
