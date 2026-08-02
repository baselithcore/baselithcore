"""Tests for Coding Agent."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agents.coding import CodeLanguage, CodingAgent


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


class _SandboxStub:
    """Stub exposing ONLY the real SandboxService API (execute_code_async).

    Regression guard for the historical bug where the agent called a
    non-existent ``sandbox.execute`` and the AttributeError was silently
    swallowed: with this stub, calling any other method fails the test.
    """

    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def execute_code_async(self, code, language="python", timeout=None):
        self.calls.append({"code": code, "language": language, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.result


def _exec_result(stdout="", stderr="", exit_code=0, execution_time=0.0):
    # Mirrors core.services.sandbox.service.ExecutionResult without importing
    # the module (its docker dependency is unavailable in some test envs).
    return MagicMock(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        execution_time=execution_time,
    )


@pytest.mark.asyncio
async def test_execute_code_calls_real_sandbox_api(coding_agent):
    sandbox = _SandboxStub(
        result=_exec_result(stdout="hello\n", exit_code=0, execution_time=0.25)
    )
    coding_agent._sandbox = sandbox

    result = await coding_agent._execute_code("print('hello')")

    assert len(sandbox.calls) == 1
    assert sandbox.calls[0]["language"] == "python"
    assert result.success is True
    assert result.output == "hello\n"
    assert result.execution_time_ms == 250.0


@pytest.mark.asyncio
async def test_execute_code_maps_nonzero_exit_to_failure(coding_agent):
    sandbox = _SandboxStub(
        result=_exec_result(stderr="Traceback: boom", exit_code=1, execution_time=0.1)
    )
    coding_agent._sandbox = sandbox

    result = await coding_agent._execute_code("raise RuntimeError('boom')")

    assert result.success is False
    assert "boom" in result.error


@pytest.mark.asyncio
async def test_execute_code_infra_failure_degrades_gracefully(coding_agent):
    sandbox = _SandboxStub(error=RuntimeError("docker unavailable"))
    coding_agent._sandbox = sandbox

    result = await coding_agent._execute_code("print(1)")

    assert result.success is False
    assert "docker unavailable" in result.error
