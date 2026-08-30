"""Compute metering and budget-kill tests for SandboxService.

Docker and sbx backends are fully mocked — no real containers are started.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config.sandbox import SandboxConfig
from core.orchestration.limits import BudgetExceededError, LoopBudget, LoopLimits


@pytest.fixture
def mock_docker_pkg():
    """Patch the docker package where docker_factory imports it."""
    with patch("core.services.sandbox.docker_factory.docker") as mock_docker:

        class MockDockerException(Exception):
            pass

        mock_docker.errors.DockerException = MockDockerException
        yield mock_docker


@pytest.fixture
def mock_docker_client(mock_docker_pkg):
    client = MagicMock()
    mock_docker_pkg.from_env.return_value = client
    return client


@pytest.fixture
def mock_container(mock_docker_client):
    """A container whose wait() takes a measurable ~10ms wall-clock."""
    container = MagicMock()
    container.logs.side_effect = lambda stdout=True, stderr=True: (
        b"test output" if stdout else b""
    )
    container.wait.side_effect = lambda timeout=None: (
        time.sleep(0.01),
        {"StatusCode": 0},
    )[1]
    mock_docker_client.containers.run.return_value = container
    return container


def patched_config(**overrides):
    """Patch the config seam the service reads, with field overrides."""
    return patch(
        "core.services.sandbox.service.get_sandbox_config",
        return_value=SandboxConfig(**overrides),
    )


def test_execution_result_metering_defaults():
    from core.services.sandbox.service import ExecutionResult

    result = ExecutionResult(stdout="", stderr="", exit_code=0, execution_time=1.0)
    assert result.compute_seconds == 0.0
    assert result.cost_usd == 0.0


def test_config_cost_rate_defaults_to_zero():
    assert SandboxConfig().cost_per_compute_second == 0.0


async def test_compute_seconds_and_cost_populated(mock_docker_client, mock_container):
    from core.services.sandbox.service import SandboxService

    with patched_config(cost_per_compute_second=0.5):
        service = SandboxService()
        result = await service.execute_code_async('print("hi")')

    assert result.exit_code == 0
    assert result.compute_seconds > 0.0
    assert result.compute_seconds == pytest.approx(result.execution_time)
    assert result.cost_usd == pytest.approx(result.compute_seconds * 0.5)


async def test_zero_rate_keeps_cost_zero(mock_docker_client, mock_container):
    from core.services.sandbox.service import SandboxService

    with patched_config():
        service = SandboxService()
        result = await service.execute_code_async('print("hi")')

    assert result.compute_seconds == pytest.approx(result.execution_time)
    assert result.cost_usd == 0.0


async def test_budget_charged_after_execution(mock_docker_client, mock_container):
    from core.services.sandbox.service import SandboxService

    budget = LoopBudget(limits=LoopLimits(budget_usd=10.0))
    with patched_config(cost_per_compute_second=1.0):
        service = SandboxService()
        result = await service.execute_code_async('print("hi")', budget=budget)

    assert result.cost_usd > 0.0
    assert budget.cost_usd == pytest.approx(result.cost_usd)


async def test_budget_exceeded_propagates(mock_docker_client, mock_container):
    from core.services.sandbox.service import SandboxService

    budget = LoopBudget(limits=LoopLimits(budget_usd=1e-9))
    with patched_config(cost_per_compute_second=1.0):
        service = SandboxService()
        with pytest.raises(BudgetExceededError):
            await service.execute_code_async('print("hi")', budget=budget)


async def test_timeout_records_compute_and_cost(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    container = MagicMock()
    container.wait.side_effect = RuntimeError("simulated wait timeout")
    mock_docker_client.containers.run.return_value = container

    budget = LoopBudget(limits=LoopLimits(budget_usd=1000.0))
    with patched_config(cost_per_compute_second=2.0):
        service = SandboxService()
        result = await service.execute_code_async(
            'print("hi")', timeout=3, budget=budget
        )

    assert result.exit_code == 124
    assert result.compute_seconds == pytest.approx(3.0)
    assert result.cost_usd == pytest.approx(6.0)
    assert budget.cost_usd == pytest.approx(6.0)
    container.kill.assert_called_once()


async def test_static_analysis_block_no_container_no_charge(mock_docker_client):
    from core.services.sandbox.service import SandboxService

    budget = LoopBudget()
    with patched_config(static_analysis_mode="block", cost_per_compute_second=1.0):
        service = SandboxService()
        result = await service.execute_code_async("import socket", budget=budget)

    assert result.exit_code == 1
    assert "blocked imports" in result.stderr
    assert result.compute_seconds == 0.0
    assert result.cost_usd == 0.0
    assert budget.cost_usd == 0.0
    mock_docker_client.containers.run.assert_not_called()


async def test_sbx_metering(mock_docker_pkg):
    from core.services.sandbox.service import SandboxService

    sbx_factory = MagicMock()
    sbx_factory.ensure_available = AsyncMock()
    sbx_factory.client.run = AsyncMock(return_value=("out", "", 0))

    with patched_config(cost_per_compute_second=0.25):
        service = SandboxService(sbx_factory=sbx_factory, provider="sbx")
        result = await service.execute_code_async('print("hi")')

    assert result.stdout == "out"
    assert result.compute_seconds == pytest.approx(result.execution_time)
    assert result.cost_usd == pytest.approx(result.compute_seconds * 0.25)
