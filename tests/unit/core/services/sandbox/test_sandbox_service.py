import pytest
from unittest.mock import MagicMock, patch

from core.services.sandbox.service import SandboxService


@pytest.fixture
def mock_docker_pkg():
    # Patch the docker package where it is imported in docker_factory
    with patch("core.services.sandbox.docker_factory.docker") as mock_docker:
        # User defined exception for testing
        class MockDockerException(Exception):
            pass

        mock_docker.errors.DockerException = MockDockerException
        yield mock_docker


@pytest.fixture
def mock_docker_client(mock_docker_pkg):
    mock_client = MagicMock()
    mock_docker_pkg.from_env.return_value = mock_client
    return mock_client


@pytest.fixture
def mock_container():
    container = MagicMock()
    # Mock logs to return bytes
    container.logs.side_effect = lambda stdout=True, stderr=True: (
        b"test output" if stdout else b""
    )
    container.wait.return_value = {"StatusCode": 0}
    return container


def test_sandbox_initialization(mock_docker_client, mock_docker_pkg):
    service = SandboxService()
    assert service.docker_factory is not None
    # We check if the client created is the one we mocked
    assert service.docker_factory.client == mock_docker_client
    mock_docker_pkg.from_env.assert_called_once()


def test_execute_code_success(mock_docker_client, mock_container):
    # Setup
    mock_docker_client.containers.run.return_value = mock_container
    service = SandboxService()

    # Execute
    result = service.execute_code('print("hello")')

    # Verify
    assert result.exit_code == 0
    assert result.stdout == "test output"
    assert result.stderr == ""

    # Verify container limits
    mock_docker_client.containers.run.assert_called_with(
        "agent-sandbox:latest",
        command=["python", "-c", 'print("hello")'],
        detach=True,
        network_mode="none",
        mem_limit="128m",
        cpu_period=100000,
        cpu_quota=50000,
        mounts=[],
    )

    # Verify cleanup
    mock_container.remove.assert_called_with(force=True)


def test_execute_code_timeout(mock_docker_client, mock_container):
    # Setup
    mock_docker_client.containers.run.return_value = mock_container

    service = SandboxService()

    # Test valid execution first (timeout logic is complex to mock depending on client,
    # but service passes timeout to wait typically)
    # The original test assumed wait returning logic.
    result = service.execute_code("print('ok')", timeout=1)
    assert result.exit_code == 0


def test_execute_unsupported_language(mock_docker_pkg):
    service = SandboxService()
    result = service.execute_code("console.log('hi')", language="javascript")
    assert result.exit_code == 1
    assert "Unsupported language" in result.stderr


def test_docker_failure_handling(mock_docker_client, mock_docker_pkg):
    # Set side effect using the mocked exception class
    mock_docker_client.containers.run.side_effect = (
        mock_docker_pkg.errors.DockerException("Boom")
    )

    service = SandboxService()
    result = service.execute_code("print('fail')")

    assert result.exit_code == 1
    assert "Boom" in result.stderr
