"""
Tree Data Structures for Thinking Patterns.

Defines the fundamental `ThoughtNode` required for Tree of Thoughts
(ToT) reasoning. Includes visualization utilities for exporting reasoning
traces to Mermaid diagrams or JSON formats.
"""

import json
from core.observability.logging import get_logger
from dataclasses import dataclass, field
from typing import List, Optional

logger = get_logger(__name__)


@dataclass
class ThoughtNode:
    """
    A single node in a reasoning tree.

    Stores the content of a specific 'thought', its evaluation score,
    and metadata required for tree traversal and MCTS optimization.
    """

    content: str
    score: float = 0.0
    depth: int = 0
    parent: Optional["ThoughtNode"] = None
    children: List["ThoughtNode"] = field(default_factory=list)
    # MCTS fields
    visits: int = 0
    value: float = 0.0

    def get_path(self) -> List[str]:
        """
        Trace the path from the root node to this specific thought.

        Returns:
            List[str]: A sequential list of thought contents representing
                       the reasoning chain.
        """
        path: List[str] = []
        curr: Optional["ThoughtNode"] = self
        while curr is not None:
            path.append(curr.content)
            curr = curr.parent
        return list(reversed(path))

    def to_dict(self) -> dict:
        """
        Recursively convert the thought node and its descendants to a dictionary.

        Returns:
            dict: A nested representation suitable for JSON serialization.
        """
        return {
            "content": self.content,
            "score": self.score,
            "depth": self.depth,
            "visits": self.visits,
            "value": self.value,
            "children": [child.to_dict() for child in self.children],
        }


def export_tree_to_json(root: ThoughtNode, filename: str) -> None:
    """Export the thought tree to a JSON file."""
    with open(filename, "w") as f:
        json.dump(root.to_dict(), f, indent=2)


def export_tree_to_mermaid(root: ThoughtNode) -> str:
    """Generate Mermaid flowchart from the tree."""
    lines = ["graph TD"]

    def traverse(node: ThoughtNode) -> None:
        """
        Recursively construct Mermaid nodes and edges.

        Args:
            node: The current ThoughtNode in the traversal.
        """
        node_id = id(node)
        # Sanitization basic
        label = (
            node.content.replace('"', "'")[:20] + "..."
            if len(node.content) > 20
            else node.content
        )
        lines.append(f'    {node_id}["{label}<br>s={node.score:.2f}"]')

        for child in node.children:
            child_id = id(child)
            lines.append(f"    {node_id} --> {child_id}")
            traverse(child)

    traverse(root)
    return "\n".join(lines)
