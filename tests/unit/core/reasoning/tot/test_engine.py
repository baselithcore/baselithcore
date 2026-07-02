"""Tests for core.reasoning.tot.engine."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.reasoning.tot.engine import TreeOfThoughts, TreeOfThoughtsAsync
from core.reasoning.tot.cache import get_thought_cache
from core.reasoning.tot.tree import ThoughtNode


@pytest.fixture(autouse=True)
def clear_thought_cache():
    """Isolate tests from the global ThoughtCache singleton."""
    get_thought_cache().clear()
    yield
    get_thought_cache().clear()


@pytest.fixture
def mock_llm_service():
    # Mirrors the real LLMService surface: one async generate_response
    # coroutine. No generate_response_async — that method does not exist on
    # LLMService, and mocking it would mask API-mismatch regressions.
    service = MagicMock()
    service.generate_response = AsyncMock()
    return service


@pytest.fixture
def tot_engine(mock_llm_service):
    return TreeOfThoughts(llm_service=mock_llm_service)


@pytest.fixture
def tot_async_engine(mock_llm_service):
    return TreeOfThoughtsAsync(llm_service=mock_llm_service)


@pytest.mark.asyncio
class TestTreeOfThoughts:
    async def test_generate_thoughts_parsing(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.return_value = (
            "1. Thought one\n2. Thought two"
        )
        node = ThoughtNode(content="root")
        thoughts = await tot_engine._generate_thoughts(node, k=2, problem="test")

        assert len(thoughts) == 2
        assert thoughts[0].content == "Thought one"
        assert thoughts[1].content == "Thought two"
        assert thoughts[0].depth == node.depth + 1

    async def test_generate_thoughts_fallback(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.return_value = "Just a thought"
        node = ThoughtNode(content="root")
        thoughts = await tot_engine._generate_thoughts(node, k=2, problem="test")

        assert len(thoughts) == 1
        assert thoughts[0].content == "Just a thought"

    async def test_evaluate_thoughts(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.side_effect = ["0.9", "0.1"]
        nodes = [ThoughtNode("t1"), ThoughtNode("t2")]
        scores = await tot_engine._evaluate_thoughts(nodes, problem="test")

        assert len(scores) == 2
        assert sorted(scores, reverse=True) == [0.9, 0.1]

    async def test_evaluate_thoughts_error(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.side_effect = Exception("API fail")
        nodes = [ThoughtNode("t1")]
        scores = await tot_engine._evaluate_thoughts(nodes, problem="test")
        assert scores[0] == 0.0


@pytest.mark.asyncio
class TestTreeOfThoughtsAsync:
    async def test_generate_thought_single_async(
        self, tot_async_engine, mock_llm_service
    ):
        mock_llm_service.generate_response.return_value = "1. Async Thought"
        node = ThoughtNode("root")
        thought = await tot_async_engine._generate_thought_single_async(node, "prob")
        assert thought.content == "Async Thought"

    async def test_evaluate_thought_single_async(
        self, tot_async_engine, mock_llm_service
    ):
        mock_llm_service.generate_response.return_value = "0.75"
        node = ThoughtNode("t1")
        score = await tot_async_engine._evaluate_thought_single_async(node, "prob")
        assert score == 0.75

    async def test_expand_with_tools(self, tot_engine, mock_llm_service):
        # Testing _expand method which supports tools
        mock_llm_service.generate_response.return_value = (
            "1. Run code [EXECUTE]print('hello')[/EXECUTE]"
        )

        mock_tool = AsyncMock()
        mock_result = MagicMock()
        mock_result.exit_code = 0
        mock_result.stdout = "hello"
        mock_tool.execute_code_async.return_value = mock_result

        tot_engine.tools = [mock_tool]

        node = ThoughtNode("root")
        thoughts = await tot_engine._expand(node, k=1, problem="test")

        assert len(thoughts) == 1
        assert "print('hello')" in thoughts[0].content
        assert "[RESULT]\nhello\n[/RESULT]" in thoughts[0].content

    async def test_solve_mcts_async(self, tot_async_engine):
        # Simplify mocks for full solve
        with (
            patch.object(tot_async_engine, "_generate_thoughts_async") as mock_gen,
            patch.object(tot_async_engine, "_evaluate_thoughts_async") as mock_eval,
        ):
            mock_gen.return_value = [ThoughtNode("t1")]
            mock_eval.return_value = [1.0]

            result = await tot_async_engine.solve(
                problem="test", k=1, max_steps=1, iterations=1
            )

            assert result["solution"] == "t1"
            assert result["steps"] == ["test", "t1"]
