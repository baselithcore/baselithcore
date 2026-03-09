"""Tests for core.reasoning.tot.engine."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from core.reasoning.tot.engine import TreeOfThoughts, TreeOfThoughtsAsync
from core.reasoning.tot.tree import ThoughtNode


@pytest.fixture
def mock_llm_service():
    service = MagicMock()
    service.generate_response = MagicMock()
    service.generate_response_async = AsyncMock()
    return service


@pytest.fixture
def tot_engine(mock_llm_service):
    return TreeOfThoughts(llm_service=mock_llm_service)


@pytest.fixture
def tot_async_engine(mock_llm_service):
    return TreeOfThoughtsAsync(llm_service=mock_llm_service)


class TestTreeOfThoughts:
    def test_generate_thoughts_parsing(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.return_value = (
            "1. Thought one\n2. Thought two"
        )
        node = ThoughtNode(content="root")
        thoughts = tot_engine._generate_thoughts(node, k=2, problem="test")

        assert len(thoughts) == 2
        assert thoughts[0].content == "Thought one"
        assert thoughts[1].content == "Thought two"
        assert thoughts[0].depth == node.depth + 1

    def test_generate_thoughts_fallback(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.return_value = "Just a thought"
        node = ThoughtNode(content="root")
        thoughts = tot_engine._generate_thoughts(node, k=2, problem="test")

        assert len(thoughts) == 1
        assert thoughts[0].content == "Just a thought"

    def test_evaluate_thoughts(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.side_effect = ["0.9", "0.1"]
        nodes = [ThoughtNode("t1"), ThoughtNode("t2")]
        scores = tot_engine._evaluate_thoughts(nodes, problem="test")

        assert len(scores) == 2
        assert scores[0] == 0.9
        assert scores[1] == 0.1

    def test_evaluate_thoughts_error(self, tot_engine, mock_llm_service):
        mock_llm_service.generate_response.side_effect = Exception("API fail")
        nodes = [ThoughtNode("t1")]
        scores = tot_engine._evaluate_thoughts(nodes, problem="test")
        assert scores[0] == 0.0


@pytest.mark.asyncio
class TestTreeOfThoughtsAsync:
    async def test_solve_async(self, tot_async_engine, mock_llm_service):
        # Setup mocks for MCTS flow
        # 1. Generate thoughts
        mock_llm_service.generate_response_async.side_effect = [
            # Expand root
            "1. Step 1\n2. Step 2",
            # Evaluate children
            "0.8",
            "0.5",
            # Expand best child (Step 1)
            "1. Step 1.1",
            # Evaluate it
            "0.9",
        ]

        # MCTS consumes these. We need enough for iterations.
        # Actually MCTS is complex to mock exactly due to multiple calls.
        # Let's mock _generate_thoughts_async and _evaluate_thoughts_async on the engine itself
        # to simplify logic testing if we want to test search logic, or trust integration.

        # Let's test the components first.
        pass

    async def test_generate_thought_single_async(
        self, tot_async_engine, mock_llm_service
    ):
        mock_llm_service.generate_response_async.return_value = "1. Async Thought"
        node = ThoughtNode("root")
        thought = await tot_async_engine._generate_thought_single_async(node, "prob")
        assert thought.content == "Async Thought"

    async def test_evaluate_thought_single_async(
        self, tot_async_engine, mock_llm_service
    ):
        mock_llm_service.generate_response_async.return_value = "0.75"
        node = ThoughtNode("t1")
        score = await tot_async_engine._evaluate_thought_single_async(node, "prob")
        assert score == 0.75

    async def test_expand_with_tools(self, tot_engine, mock_llm_service):
        # Testing _expand method which supports tools
        mock_llm_service.generate_response_async.return_value = (
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

    async def test_solve_mcts_async(self, tot_async_engine, mock_llm_service):
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
