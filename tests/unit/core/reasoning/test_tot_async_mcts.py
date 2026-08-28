from unittest.mock import AsyncMock, MagicMock

import pytest

from core.reasoning.tot import ThoughtNode, TreeOfThoughtsAsync
from core.reasoning.tot.cache import get_thought_cache
from core.reasoning.tot.mcts import mcts_search_async


@pytest.fixture(autouse=True)
def clear_thought_cache():
    """Isolate tests from the global ThoughtCache singleton."""
    get_thought_cache().clear()
    yield
    get_thought_cache().clear()


@pytest.fixture
def mock_llm_service():
    # Mirrors the real LLMService surface: one async generate_response.
    service = MagicMock()

    async def async_gen(prompt):
        if "eval" in prompt.lower() or "score" in prompt.lower():
            return "0.8"
        return "1. Option A\n2. Option B\n3. Option C"

    service.generate_response = AsyncMock(side_effect=async_gen)
    return service


@pytest.mark.asyncio
async def test_solve_async_mcts_strategy(mock_llm_service):
    """Test that solve_async with strategy='mcts' calls the MCTS logic."""
    tot = TreeOfThoughtsAsync(llm_service=mock_llm_service)

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

    # Verify LLM was called through the real async entrypoint: at least one
    # batched generation plus one evaluation per generated child.
    assert mock_llm_service.generate_response.called
    assert mock_llm_service.generate_response.call_count >= 3


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


class TestAsyncMCTSBounds:
    """The loop only counted iterations: the orchestrator ticks the LoopBudget
    once for the whole flow, so nothing inside consulted the deadline, and a
    search that had stopped improving kept paying ~4 LLM round trips per
    iteration."""

    @staticmethod
    def _flat_generator(score: float):
        async def generator(node, branching_factor, problem):
            return [
                ThoughtNode(content=f"c{i}", depth=node.depth + 1)
                for i in range(branching_factor)
            ]

        async def evaluator(children, problem):
            return [score] * len(children)

        return generator, evaluator

    async def test_search_stops_once_it_stops_improving(self):
        calls = {"n": 0}
        generator, evaluator = self._flat_generator(0.5)

        async def counting_generator(node, branching_factor, problem):
            calls["n"] += 1
            return await generator(node, branching_factor, problem)

        root = ThoughtNode(content="root", depth=0)
        await mcts_search_async(
            root,
            max_depth=10,
            generator=counting_generator,
            evaluator=evaluator,
            iterations=50,
            patience=3,
        )

        # Scores never beat the first expansion, so the search gives up well
        # before the 50th iteration.
        assert calls["n"] < 15

    async def test_patience_none_runs_the_full_count(self):
        calls = {"n": 0}
        generator, evaluator = self._flat_generator(0.5)

        async def counting_generator(node, branching_factor, problem):
            calls["n"] += 1
            return await generator(node, branching_factor, problem)

        root = ThoughtNode(content="root", depth=0)
        await mcts_search_async(
            root,
            max_depth=10,
            generator=counting_generator,
            evaluator=evaluator,
            iterations=6,
            patience=None,
        )

        assert calls["n"] == 6

    async def test_expired_budget_deadline_stops_the_search(self, monkeypatch):
        from core.orchestration.limits import BudgetExceededError

        class _ExpiredBudget:
            def check_deadline(self):
                raise BudgetExceededError("max_seconds", None)

        monkeypatch.setattr(
            "core.orchestration.budget_context.get_active_budget",
            lambda: _ExpiredBudget(),
        )

        generator, evaluator = self._flat_generator(0.5)
        root = ThoughtNode(content="root", depth=0)

        with pytest.raises(BudgetExceededError):
            await mcts_search_async(
                root,
                max_depth=10,
                generator=generator,
                evaluator=evaluator,
                iterations=50,
            )
