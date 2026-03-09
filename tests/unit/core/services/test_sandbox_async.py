import pytest
from unittest.mock import MagicMock, AsyncMock
from core.services.sandbox.service import SandboxService, ExecutionResult


@pytest.mark.asyncio
async def test_sandbox_execute_async():
    # Mock DockerFactory
    mock_factory = MagicMock()
    mock_factory.ensure_image = AsyncMock()
    # Mock container
    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.side_effect = [b"hello world\n", b""]

    mock_factory.client.containers.run.return_value = mock_container

    service = SandboxService(docker_factory=mock_factory)

    # Execute async
    result = await service.execute_code_async("print('hello world')")

    assert isinstance(result, ExecutionResult)
    assert result.stdout == "hello world"
    assert result.exit_code == 0

    # Verify container run called with default args
    mock_factory.client.containers.run.assert_called_once()
    args, kwargs = mock_factory.client.containers.run.call_args
    assert kwargs["network_mode"] == "none"


@pytest.mark.asyncio
async def test_sandbox_mounts():
    mock_factory = MagicMock()
    mock_factory.ensure_image = AsyncMock()
    mock_container = MagicMock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.return_value = b""
    mock_factory.client.containers.run.return_value = mock_container

    service = SandboxService(docker_factory=mock_factory)

    mounts = {"/tmp/host": "/data"}
    await service.execute_code_async("print('hi')", mounts=mounts)

    # Verify mounts passed
    args, kwargs = mock_factory.client.containers.run.call_args
    assert "mounts" in kwargs
    # We can check if Mount objects were created, but exact check depends on docker import
    assert len(kwargs["mounts"]) == 1
    mount = kwargs["mounts"][0]
    # Mount is dict-like
    assert mount["Target"] == "/data"
    assert mount["Source"] == "/tmp/host"
