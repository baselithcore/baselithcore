"""
Test suite for MCTS strategy in Tree of Thoughts.

Tests the modularized MCTS functions (uct_select, backpropagate, mcts_search)
from core.reasoning.tot.mcts module.
"""

from core.reasoning.tot import ThoughtNode, uct_select, backpropagate, mcts_search


class TestMCTS:
    """Test suite for MCTS strategy in Tree of Thoughts."""

    def test_thought_node_mcts_fields(self):
        """Test new MCTS fields in ThoughtNode."""
        node = ThoughtNode(content="root")
        assert node.visits == 0
        assert node.value == 0.0

    def test_backpropagation(self):
        """Test value backpropagation using module function."""
        root = ThoughtNode(content="root")
        child = ThoughtNode(content="child", parent=root)
        leaf = ThoughtNode(content="leaf", parent=child)

        # 1. Update leaf
        backpropagate(leaf, 1.0)

        assert leaf.visits == 1
        assert leaf.value == 1.0
        assert child.visits == 1
        assert child.value == 1.0
        assert root.visits == 1
        assert root.value == 1.0

        # 2. Update leaf with 0.0
        backpropagate(leaf, 0.0)

        assert leaf.visits == 2
        assert leaf.value == 0.5  # (1+0)/2
        assert child.visits == 2
        assert child.value == 0.5
        assert root.visits == 2
        assert root.value == 0.5

    def test_uct_select(self):
        """Test UCT selection logic using module function."""
        root = ThoughtNode(content="root", visits=10, value=0.5)

        # Child 1: High value, high visits (exploitation)
        c1 = ThoughtNode(content="c1", parent=root, visits=5, value=0.9)
        # Child 2: No visits (exploration)
        c2 = ThoughtNode(content="c2", parent=root, visits=0, value=0.0)

        root.children = [c1, c2]

        # Should pick unvisited first
        selected = uct_select(root)
        assert selected == c2

        # Now visit c2
        c2.visits = 1
        c2.value = 0.4

        # Both visited, check UCT calculation
        # c1 UCT = 0.9 + sqrt(2 * ln(11) / 5) ≈ 0.9 + sqrt(4.79 / 5) ≈ 0.9 + 0.97 = 1.87
        # c2 UCT = 0.4 + sqrt(2 * ln(11) / 1) ≈ 0.4 + sqrt(4.79) ≈ 0.4 + 2.18 = 2.58
        # Should pick c2 due to high exploration bonus relative to visits
        selected_2 = uct_select(root)
        assert selected_2 == c2

    def test_mcts_search_flow(self):
        """Test full MCTS search flow using module function."""
        root = ThoughtNode(content="root")

        # Generators
        def generator(node):
            if node.content == "root":
                return [
                    ThoughtNode(content="child1", depth=1),
                    ThoughtNode(content="child2", depth=1),
                ]
            if node.content == "child1":
                return [ThoughtNode(content="leaf1", depth=2)]
            return []

        # Evaluator
        def evaluator(nodes):
            scores = []
            for n in nodes:
                if n.content == "child1":
                    scores.append(0.8)
                elif n.content == "child2":
                    scores.append(0.2)
                elif n.content == "leaf1":
                    scores.append(1.0)
                else:
                    scores.append(0.0)
            return scores

        best_node = mcts_search(
            root, max_depth=2, generator=generator, evaluator=evaluator, iterations=10
        )

        assert best_node is not None
        # Should find leaf1 as it leads to 1.0 (child1 -> leaf1)
        # Or child1 itself if it's the best node tracked
        # The logic tracks 'best_node' as the one with highest score encountered
        assert best_node.content in ["leaf1", "child1"]
        # Check visits
        assert root.visits == 10
