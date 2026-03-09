import pytest
from unittest.mock import Mock, patch
from core.reasoning.cot import ChainOfThought


class TestChainOfThought:
    """Tests for ChainOfThought."""

    @pytest.mark.asyncio
    async def test_initialization(self):
        """Test initialization."""
        cot = ChainOfThought()
        assert cot._llm_service is None

    @pytest.mark.asyncio
    async def test_reason_no_llm_service(self):
        """Test reason without LLM service."""
        cot = ChainOfThought()
        # Mock get_llm_service to return None or raise import error effectively
        with patch("core.services.llm.get_llm_service", side_effect=ImportError):
            answer, steps = await cot.reason("test question")

        assert answer == "Unable to reason without LLM service"
        assert len(steps) == 3
        assert steps[0].thought == "Analyzing: test question"

    @pytest.mark.asyncio
    async def test_reason_with_llm(self):
        """Test reasoning with LLM service."""
        mock_llm = Mock()
        # Note: generate_response is sync, but wrapped in to_thread. Mock is sync.
        mock_llm.generate_response.return_value = """
1. Step one thought process.
2. Step two thought process.
Answer: The answer is 42.
"""
        cot = ChainOfThought(llm_service=mock_llm)

        answer, steps = await cot.reason("What is life?")

        assert len(steps) == 2
        assert steps[0].step_number == 1
        assert steps[0].thought == "Step one thought process."
        assert "The answer is 42" in answer

    @pytest.mark.asyncio
    async def test_parse_reasoning_robustness(self):
        """Test robust parsing of various formats."""
        cot = ChainOfThought()

        # Case 1: Standard
        text1 = "1. First step\n2. Second step\nAnswer: Done"
        steps1, ans1 = cot._parse_reasoning(text1)
        assert len(steps1) == 2
        assert "Done" in ans1

        # Case 2: Parenthesis
        text2 = "1) First step\n2) Second step\nConclusion: Done"
        steps2, ans2 = cot._parse_reasoning(text2)
        assert len(steps2) == 2

        # Case 3: "Step X" format
        text3 = "Step 1: First step\nStep 2: Second step\nFinal Answer: Done"
        steps3, ans3 = cot._parse_reasoning(text3)
        assert len(steps3) == 2
        assert steps3[0].thought == "First step"

        # Case 4: Messy newlines
        text4 = "1. Step A\nContinuation of A\n2. Step B\n\nAnswer: Result"
        steps4, ans4 = cot._parse_reasoning(text4)
        assert len(steps4) == 2
        assert "Continuation of A" in steps4[0].thought
        assert "Result" in ans4

    @pytest.mark.asyncio
    async def test_reason_error_handling(self):
        """Test error handling during generation."""
        mock_llm = Mock()
        mock_llm.generate_response.side_effect = Exception("LLM Error")

        cot = ChainOfThought(llm_service=mock_llm)
        answer, steps = await cot.reason("Fail me")

        assert answer == "Unable to reason without LLM service"
        assert len(steps) == 3
