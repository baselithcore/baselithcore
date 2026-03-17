from unittest.mock import MagicMock
from core.graph.retrieval import get_subgraph_for_node, search_node_by_property


class TestGraphDBRetrieval:
    def test_search_node_by_property_found(self):
        """Test search_node_by_property when node is found."""
        mock_query = MagicMock()
        # Mock result: List of rows, where row is a list containing the ID
        mock_query.return_value = [["found_id"]]

        result = search_node_by_property(mock_query, "name", "test_val")

        assert result == "found_id"
        mock_query.assert_called_once()
        args, kwargs = mock_query.call_args
        assert "MATCH (n {tenant_id: $tenant_id}) WHERE n.name = $val" in args[0]
        assert args[1] == {"val": "test_val"}

    def test_search_node_by_property_not_found(self):
        """Test search_node_by_property when no node is found."""
        mock_query = MagicMock()
        mock_query.return_value = []

        result = search_node_by_property(mock_query, "name", "missing")
        assert result is None

    def test_search_node_by_property_invalid_prop(self):
        """Test search_node_by_property limits injections."""
        mock_query = MagicMock()
        result = search_node_by_property(mock_query, "invalid-name;", "val")
        assert result is None
        mock_query.assert_not_called()

    def test_get_subgraph_empty(self):
        """Test get_subgraph_for_node with no results."""
        mock_query = MagicMock()
        mock_query.return_value = []

        result = get_subgraph_for_node(mock_query, "id_1")

        assert result["nodes"] == []
        assert result["links"] == []

    def test_get_subgraph_compact_format(self):
        """Test parsing of RedisGraph compact format."""
        mock_query = MagicMock()

        # Construct a compact response
        # Header: [ver, [Labels], [Props]]
        # Labels: 0="Document", 1="Story"
        # Props: 0="id", 1="name"
        header = [1, ["Document", "Story"], ["id", "name"]]

        # Node 1 (Center): [type_ignored, [id_internal, [label_ids], [[prop_id, type, val]]]]
        # id="node_1", name="Center Node", Label=Document(0)
        node1 = [None, [100, [0], [[0, 1, "node_1"], [1, 1, "Center Node"]]]]

        # Node 2 (Neighbor): id="node_2", name="Neighbor", Label=Story(1)
        node2 = [None, [101, [1], [[0, 1, "node_2"], [1, 1, "Neighbor"]]]]

        # Relation: [rel_type_id, extra_data...] or object
        # The code handles various rel formats. Let's try object first if supported,
        # or list. Code checks `hasattr(rel, "relation")` or `rel[1]`.
        # Let's use a mock object for relation for simplicity in first pass,
        # verifying object-based fallback support involved in code.
        mock_rel = MagicMock()
        mock_rel.relation = "RELATED_TO"

        # Data row: [node1, rel, node2]
        row = [node1, mock_rel, node2]

        mock_query.return_value = [header, [row]]

        result = get_subgraph_for_node(mock_query, "node_1")

        assert len(result["nodes"]) == 2

        nodes_dict = {n["id"]: n for n in result["nodes"]}
        assert "node_1" in nodes_dict
        assert "node_2" in nodes_dict
        assert nodes_dict["node_1"]["label"] == "Center Node"
        assert nodes_dict["node_1"]["group"] == "Document"
        assert nodes_dict["node_2"]["group"] == "Story"

        assert len(result["links"]) == 1
        link = result["links"][0]
        assert link["source"] == "node_1"
        assert link["target"] == "node_2"
        assert link["label"] == "RELATED_TO"

    def test_get_subgraph_fallback_labels(self):
        """Test fallback label mapping when header is missing."""
        mock_query = MagicMock()

        # No header, just data
        header = []

        # Node with ID 1 (no longer in fallback map)
        # Props: 0 (fallback to id), 1 (fallback to name)
        node1 = [None, [100, [1], [[0, 1, "fallback_id"], [1, 1, "Fallback Name"]]]]

        row = [node1]
        mock_query.return_value = [header, [row]]

        result = get_subgraph_for_node(mock_query, "fallback_id")

        assert len(result["nodes"]) == 1
        node = result["nodes"][0]
        assert node["id"] == "fallback_id"
        assert node["label"] == "Fallback Name"
        assert node["group"] == "Label_1"  # ID 1 has no fallback, so it becomes Label_1
