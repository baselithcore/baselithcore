"""
Unit Tests for Context Folding (AgentFold Pattern).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from core.memory.folding import ContextFolder, FoldingConfig
from core.memory.types import MemoryItem, MemoryType


class TestContextFolder:
    """Tests for ContextFolder class."""

    @pytest.fixture
    def mock_llm(self):
        llm = MagicMock()
        llm.generate_response = AsyncMock(
            return_value="Folded summary of conversation."
        )
        return llm

    def test_initialization(self):
        """Test basic initialization."""
        folder = ContextFolder()
        assert folder.config.keep_latest_n == 3
        assert folder.config.fold_threshold_chars == 2000

    @pytest.mark.asyncio
    async def test_fold_empty_history(self):
        """Test folding empty history."""
        folder = ContextFolder()
        result = await folder.fold([])
        assert result == ""

    @pytest.mark.asyncio
    async def test_fold_below_threshold(self):
        """Test folding when history is small (no actual folding needed logic handled in caller usually, but check folding behavior)."""
        # The fold method always attempts to fold based on keep_latest_n,
        # whereas fold_if_needed checks the total char threshold.
        folder = ContextFolder(config=FoldingConfig(keep_latest_n=1))

        history = [
            MemoryItem(content="M1", memory_type=MemoryType.SHORT_TERM),
            MemoryItem(content="M2", memory_type=MemoryType.SHORT_TERM),
        ]

        # Should fold M1, keep M2
        # Without LLM, it uses simple fallback
        result = await folder.fold(history)

        # Check for fallback summary format
        assert "[Previous context" in result
        assert "M1" in result
        assert "M2" in result
        assert result.count("[Previous context") == 1

    @pytest.mark.asyncio
    async def test_fold_with_llm(self, mock_llm):
        """Test folding using LLM service."""
        folder = ContextFolder(
            llm_service=mock_llm, config=FoldingConfig(keep_latest_n=1)
        )

        history = [
            MemoryItem(content="Old message", memory_type=MemoryType.SHORT_TERM),
            MemoryItem(content="New message", memory_type=MemoryType.SHORT_TERM),
        ]

        result = await folder.fold(history)

        assert "[Previous context: Folded summary of conversation.]" in result
        assert "New message" in result
        assert "Old message" not in result  # Should be replaced by summary

    @pytest.mark.asyncio
    async def test_fold_if_needed_no_fold(self):
        """Test fold_if_needed when below threshold."""
        config = FoldingConfig(fold_threshold_chars=1000)
        folder = ContextFolder(config=config)

        history = [
            MemoryItem(content="Short message", memory_type=MemoryType.SHORT_TERM)
        ]

        context, was_folded = await folder.fold_if_needed(history)

        assert was_folded is False
        assert "Short message" in context
        assert "Previous context" not in context

    @pytest.mark.asyncio
    async def test_fold_if_needed_yes_fold(self, mock_llm):
        """Test fold_if_needed when above threshold."""
        config = FoldingConfig(fold_threshold_chars=10, keep_latest_n=0)
        folder = ContextFolder(config=config, llm_service=mock_llm)

        history = [
            MemoryItem(
                content="Long enough message to trigger fold",
                memory_type=MemoryType.SHORT_TERM,
            )
        ]

        context, was_folded = await folder.fold_if_needed(history)

        assert was_folded is True
        assert "Folded summary" in context

    def test_estimate_token_savings(self):
        """Test token saving estimation."""
        folder = ContextFolder(
            config=FoldingConfig(keep_latest_n=1, summary_max_chars=10)
        )

        # M1 (10 chars) -> Folded (max 10 chars)
        # M2 (10 chars) -> Kept (10 chars)
        history = [
            MemoryItem(content="0123456789", memory_type=MemoryType.SHORT_TERM),
            MemoryItem(content="0123456789", memory_type=MemoryType.SHORT_TERM),
        ]

        stats = folder.estimate_token_savings(history)

        assert stats["original_chars"] == 20
        # Estimated: 10 (kept) + 10 (folded max) = 20
        # Wait, folded max is 10. M1 is 10. So it estimates 10 chars for M1.
        # Savings = 0 if summary is same size as content.

        # Let's make M1 longer
        history[0].content = "0123456789" * 10  # 100 chars

        stats = folder.estimate_token_savings(history)

        assert stats["original_chars"] == 110
        assert stats["estimated_chars"] == 20  # 10 (kept) + 10 (summary max)
        assert stats["savings_chars"] == 90
