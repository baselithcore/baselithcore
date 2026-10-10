"""Tests for Coding Agent."""

from unittest.mock import MagicMock

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
async def test_run_tests_reads_the_report_the_sandbox_printed(
    coding_agent, monkeypatch
):
    from plugins.coding_agent import agent as agent_module
    from plugins.coding_agent.harness import report_marker

    monkeypatch.setattr(agent_module, "new_run_key", lambda: "k" * 32)
    line = (
        report_marker("k" * 32)
        + '{"passed": 2, "failed": 1, "errors": 0, "collection_error": false, '
    )
    line += '"failures": [["test_x", "assert 1 == 2"]], "exit": 1}'
    # The harness process ends normally after its report, whatever the tests did.
    sandbox = _SandboxStub(result=_exec_result(stdout="noise\n" + line, exit_code=0))
    coding_agent._sandbox = sandbox

    report = await coding_agent._run_tests("def f(): ...", "def test_x(): ...")

    assert (report.passed, report.failed, report.total, report.green) == (
        2,
        1,
        3,
        False,
    )
    assert "test_x" in report.evidence()
    assert (
        "test_solution.py" in sandbox.calls[0]["code"]
    )  # the harness, not the bare code


@pytest.mark.asyncio
async def test_a_sandbox_timeout_is_reported_as_one(coding_agent):
    coding_agent._sandbox = _SandboxStub(result=_exec_result(exit_code=124))

    report = await coding_agent._run_tests("x = 1", "def test_x(): ...")

    assert not report.green and "time" in report.evidence()


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
