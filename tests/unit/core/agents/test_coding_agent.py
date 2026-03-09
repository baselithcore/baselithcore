"""Tests for Coding Agent."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.agents.coding import CodingAgent, CodeLanguage


@pytest.fixture
def coding_agent():
    return CodingAgent(max_fix_attempts=2)


@pytest.mark.asyncio
async def test_init(coding_agent):
    assert coding_agent.max_fix_attempts == 2
    assert coding_agent.language == CodeLanguage.PYTHON


@pytest.mark.asyncio
async def test_generate_code(coding_agent):
    with patch.object(coding_agent, "_ask_llm", new_callable=AsyncMock) as mock_ask:
        with patch.object(
            coding_agent, "_execute_code", new_callable=AsyncMock
        ) as mock_exec:
            mock_ask.return_value = "print('hello')"
            mock_exec.return_value.success = True

            result = await coding_agent.generate_code("Print hello")

            assert result.success
            assert result.final_code == "print('hello')"
            mock_ask.assert_called_once()
            mock_exec.assert_called_once()


@pytest.mark.asyncio
async def test_fix_code_success(coding_agent):
    with patch.object(coding_agent, "_ask_llm", new_callable=AsyncMock) as mock_ask:
        with patch.object(
            coding_agent, "_execute_code", new_callable=AsyncMock
        ) as mock_exec:
            mock_ask.return_value = "fixed_code"
            mock_exec.return_value.success = True

            result = await coding_agent.fix_code("buggy", "error")

            assert result.success
            assert result.final_code == "fixed_code"
            assert result.iterations == 1


@pytest.mark.asyncio
async def test_fix_code_retry(coding_agent):
    with patch.object(coding_agent, "_ask_llm", new_callable=AsyncMock) as mock_ask:
        with patch.object(
            coding_agent, "_execute_code", new_callable=AsyncMock
        ) as mock_exec:
            # First attempt fails, second succeeds
            mock_ask.side_effect = ["fix1", "fix2"]
            mock_exec.side_effect = [
                MagicMock(success=False, error="err1"),
                MagicMock(success=True),
            ]

            result = await coding_agent.fix_code("buggy", "error")

            assert result.success
            assert result.final_code == "fix2"
            assert result.iterations == 2
