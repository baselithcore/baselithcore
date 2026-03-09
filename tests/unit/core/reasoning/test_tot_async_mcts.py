import pytest
from unittest.mock import MagicMock, AsyncMock
from core.reasoning.tot import TreeOfThoughtsAsync, ThoughtNode


@pytest.fixture
def mock_llm_service():
    service = MagicMock()
    # Mock synchronous method (not used by async MCTS, but good to have)
    service.generate_response.return_value = "1. Thought 1\n2. Thought 2\n3. Thought 3"

    # Mock async method
    async def async_gen(prompt):
        if "eval" in prompt.lower() or "score" in prompt.lower():
            return "0.8"
        return "1. Option A\n2. Option B\n3. Option C"

    service.generate_response_async = AsyncMock(side_effect=async_gen)
    return service


@pytest.mark.asyncio
async def test_solve_async_mcts_strategy(mock_llm_service):
    """Test that solve_async with strategy='mcts' calls the MCTS logic."""
    tot = TreeOfThoughtsAsync(llm_service=mock_llm_service)

    # Spy on the internal _mcts_search_async method
    # We can't easily spy on the method of the instance we are testing in Python without complex patching,
    # so we'll inspect the result structure and side effects (calls to LLM).

    problem = "How to reach Mars?"
    result = await tot.solve(
        problem=problem,
        strategy="mcts",
        iterations=5,
        max_depth=3,
        branching_factor=2,
        initial_state="Start",
    )
    path = result["steps"]

    # Check that we got a path
    assert isinstance(path, list)
    assert len(path) > 0
    assert path[0] == "Start"

    # Verify LLM was called asynchronously
    assert mock_llm_service.generate_response_async.called
    assert (
        mock_llm_service.generate_response_async.call_count >= 5
    )  # At least 5 iterations * (gen + eval)


@pytest.mark.asyncio
async def test_mcts_search_async_logic(mock_llm_service):
    """Test the internal _mcts_search_async logic directly."""
    tot = TreeOfThoughtsAsync(llm_service=mock_llm_service)
    root = ThoughtNode(content="Start", score=1.0, depth=0)

    # Run a small search
    best_node = await tot._mcts_search_async(
        root, max_depth=2, iterations=3, problem="Test MCTS", branching_factor=2
    )

    assert best_node is not None
    assert isinstance(best_node, ThoughtNode)

    # Root should have children populated
    assert len(root.children) > 0
    # Children should have visits and values updated
    assert root.children[0].visits > 0
    assert root.visits > 0


@pytest.mark.asyncio
async def test_solve_async_fallback_bfs(mock_llm_service):
    """Ensure default strategy is still working (BFS)."""
    tot = TreeOfThoughtsAsync(llm_service=mock_llm_service)

    result = await tot.solve(problem="Test BFS", strategy="bfs", max_depth=2)
    path = result["steps"]

    assert isinstance(path, list)
    assert len(path) > 0
