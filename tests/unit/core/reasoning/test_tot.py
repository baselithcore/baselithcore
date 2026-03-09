from unittest.mock import MagicMock, AsyncMock
import pytest
from core.reasoning.tot import TreeOfThoughts, ThoughtNode
from core.reasoning.search import breadth_first_search, depth_first_search


def test_thought_node_path():
    root = ThoughtNode(content="root")
    child1 = ThoughtNode(content="child1", parent=root)
    child2 = ThoughtNode(content="child2", parent=child1)

    assert child2.get_path() == ["root", "child1", "child2"]


def test_breadth_first_search_logic():
    # Mock generator: creates 2 children with deterministic names
    def generator(node):
        if node.depth >= 2:
            return []
        return [
            ThoughtNode(content=f"{node.content}_a", depth=node.depth + 1),
            ThoughtNode(content=f"{node.content}_b", depth=node.depth + 1),
        ]

    # Mock evaluator: prefers 'a' branch
    def evaluator(nodes):
        return [0.9 if "_a" in n.content else 0.1 for n in nodes]

    root = ThoughtNode(content="root", score=1.0)
    best = breadth_first_search(
        root, max_depth=2, beam_width=1, generator=generator, evaluator=evaluator
    )

    # BFS with beam_width=1 and preference for 'a' should follow: root -> root_a -> root_a_a
    assert best.content == "root_a_a"


def test_depth_first_search_logic():
    # Mock generator
    def generator(node):
        if node.depth >= 2:
            return []
        return [
            ThoughtNode(content=f"{node.content}_1", depth=node.depth + 1),
            ThoughtNode(content=f"{node.content}_2", depth=node.depth + 1),
        ]

    # Mock evaluator: Prefer paths with more '2's to test optimization
    # root_2_2 has two '2's -> score 0.9
    # root_1_2 has one '2' -> score 0.5
    # root_2_1 has one '2' -> score 0.5
    def evaluator(nodes):
        return [0.1 + 0.4 * n.content.count("2") for n in nodes]

    root = ThoughtNode(content="root", score=0.1)
    best = depth_first_search(
        root, max_depth=2, generator=generator, evaluator=evaluator, threshold=0.0
    )

    # Should find valid path to depth 2. With prioritizing 2: root -> root_2 -> root_2_2
    assert best.content == "root_2_2"


@pytest.mark.asyncio
async def test_tot_integration_mock():
    # Mock LLM service
    mock_llm = MagicMock()

    def sync_side_effect(prompt):
        """Sync version for base TreeOfThoughts methods."""
        # Evaluation check
        if (
            "Evaluate" in prompt
            or "score" in prompt.lower()
            or "thought" in prompt.lower()
            and "value" in prompt.lower()
        ):
            if "Step A" in prompt and "Step AA" not in prompt:
                return "0.9"
            if "Step B" in prompt:
                return "0.5"
            if "Step AA" in prompt:
                return "0.95"
            return "0.1"

        # Generation check
        if "Step AA" in prompt:
            return ""  # Stop expansion
        if "Step A" in prompt:
            return "1. Step AA"
        return "1. Step A\n2. Step B"

    async def async_side_effect(prompt):
        """Async version - just wraps sync logic."""
        return sync_side_effect(prompt)

    # Mock both sync and async methods
    mock_llm.generate_response = MagicMock(side_effect=sync_side_effect)
    mock_llm.generate_response_async = AsyncMock(side_effect=async_side_effect)

    tot = TreeOfThoughts(llm_service=mock_llm)

    # Use strategies that work with the mock
    result = await tot.solve(
        problem="Problem",
        strategy="mcts",
        iterations=5,
        max_steps=2,  # Explicitly pass max_steps matching max_depth requirement
        max_depth=2,  # Keep for safety if logic changes
        branching_factor=2,
        initial_state="Start",
    )

    path = result["steps"]
    assert path == ["Start", "Step A", "Step AA"]


class TestTreeOfThoughtsAsync:
    """Tests for async Tree of Thoughts."""

    async def test_solve_async_basic(self):
        """Test async solve with mock LLM."""
        from unittest.mock import AsyncMock
        from core.reasoning.tot import TreeOfThoughtsAsync

        mock_llm = MagicMock()

        async def side_effect(prompt):
            if "Evaluate" in prompt or "score" in prompt.lower():
                # Random score or sequential based on prompt content?
                return "0.85"
            return "1. Thought 1\n2. Thought 2"

        mock_llm.generate_response_async = AsyncMock(side_effect=side_effect)

        tot = TreeOfThoughtsAsync(llm_service=mock_llm)

        path_dict = await tot.solve(
            "How to solve this?",
            max_steps=2,
            branching_factor=3,
            beam_width=1,
            initial_state="Start",
        )
        path = path_dict["steps"]

        # Should have made parallel calls
        assert len(path) >= 2  # At least initial + some thoughts
        assert path[0] == "Start"
        assert mock_llm.generate_response_async.called

    async def test_generate_thoughts_async_parallel(self):
        """Test that thoughts are generated in parallel."""
        from unittest.mock import AsyncMock
        from core.reasoning.tot import TreeOfThoughtsAsync, ThoughtNode
        import time

        mock_llm = MagicMock()

        async def slow_response(prompt):
            await asyncio.sleep(0.01)  # Small delay to simulate LLM
            return "1. Generated thought"

        mock_llm.generate_response_async = AsyncMock(side_effect=slow_response)

        tot = TreeOfThoughtsAsync(llm_service=mock_llm)
        root = ThoughtNode(content="root", depth=0)

        import asyncio

        start = time.time()
        thoughts = await tot._generate_thoughts_async(root, 5, "test problem")
        elapsed = time.time() - start

        # If parallel, should take ~0.01s not ~0.05s
        assert elapsed < 0.05  # Allow some overhead
        assert len(thoughts) == 5

    async def test_evaluate_thoughts_async_parallel(self):
        """Test that evaluation runs in parallel."""
        from unittest.mock import AsyncMock
        from core.reasoning.tot import TreeOfThoughtsAsync, ThoughtNode

        mock_llm = MagicMock()
        mock_llm.generate_response_async = AsyncMock(return_value="0.85")

        tot = TreeOfThoughtsAsync(llm_service=mock_llm)
        nodes = [ThoughtNode(content=f"thought_{i}") for i in range(5)]

        scores = await tot._evaluate_thoughts_async(nodes, "test problem")

        assert len(scores) == 5
        assert all(s == 0.85 for s in scores)
        assert mock_llm.generate_response_async.call_count == 5
