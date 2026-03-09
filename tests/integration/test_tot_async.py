import pytest
from unittest.mock import MagicMock, AsyncMock
from core.reasoning.tot.engine import TreeOfThoughtsAsync


@pytest.fixture
def mock_async_llm_service():
    mock_service = MagicMock()

    # Define async side effects
    async def mock_generate(prompt: str) -> str:
        prompt_lower = prompt.lower()
        if "judge" in prompt_lower or "evaluate" in prompt_lower:
            return "0.9"  # High score
        else:
            # Thought generation
            return (
                "1. First feasible option\n2. Second feasible option\n3. Third option"
            )

    # Important: configure the async method
    mock_service.generate_response_async = AsyncMock(side_effect=mock_generate)
    # Configure sync fallback just in case
    mock_service.generate_response.return_value = "0.5"

    return mock_service


@pytest.mark.asyncio
async def test_tot_async_bfs_solve(mock_async_llm_service):
    """Test standard BFS/Beam search strategy asynchronously."""
    tot = TreeOfThoughtsAsync(llm_service=mock_async_llm_service)

    # We force the engine to use the fallback BFS loop by explicitly NOT providing a specialized strategy
    # or by providing one that falls through (the code uses 'mcts' or default loop)
    # Looking at engine.py, if strategy != 'mcts', it falls into the loop.

    result = await tot.solve(
        problem="How to optimize Python code?", k=2, max_steps=2, strategy="bfs"
    )

    assert result["solution"] != "No solution found"
    assert len(result["steps"]) > 0
    assert mock_async_llm_service.generate_response_async.called


@pytest.mark.asyncio
async def test_tot_async_mcts_solve(mock_async_llm_service):
    """Test Async MCTS strategy."""
    tot = TreeOfThoughtsAsync(llm_service=mock_async_llm_service)

    result = await tot.solve(
        problem="Complex planning problem",
        strategy="mcts",
        iterations=5,  # Keep it small for test speed
        max_steps=3,
    )

    assert result["solution"] != "No solution found"
    assert "tree_visualization" in result
