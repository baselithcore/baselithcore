import pytest
import json
from core.reasoning.tot import ThoughtNode, export_tree_to_json, export_tree_to_mermaid


class TestToTVisualization:
    """Test suite for ToT visualization and export."""

    @pytest.fixture
    def sample_tree(self):
        root = ThoughtNode(content="root", score=0.5)
        child1 = ThoughtNode(content="child1", score=0.8, parent=root)
        child2 = ThoughtNode(content="child2", score=0.2, parent=root)
        root.children = [child1, child2]
        grandchild = ThoughtNode(content="grandchild", score=0.9, parent=child1)
        child1.children = [grandchild]
        return root

    def test_to_dict(self, sample_tree):
        """Test conversion to dictionary."""
        data = sample_tree.to_dict()
        assert data["content"] == "root"
        assert len(data["children"]) == 2
        assert data["children"][0]["content"] == "child1"
        assert data["children"][0]["children"][0]["content"] == "grandchild"

    def test_export_to_json(self, sample_tree, tmp_path):
        """Test JSON export."""
        output_file = tmp_path / "tree.json"
        export_tree_to_json(sample_tree, str(output_file))

        with open(output_file, "r") as f:
            data = json.load(f)

        assert data["content"] == "root"
        assert len(data["children"]) == 2

    def test_export_to_mermaid(self, sample_tree):
        """Test Mermaid export."""
        mermaid = export_tree_to_mermaid(sample_tree)
        assert "graph TD" in mermaid
        assert "root" in mermaid
        assert "child1" in mermaid
        assert "grandchild" in mermaid
        assert "-->" in mermaid
