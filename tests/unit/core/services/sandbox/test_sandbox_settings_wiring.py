"""``SANDBOX_ENABLE_NETWORK`` and ``SANDBOX_DOCKER_SOCKET`` reach the runtime.

Regression: both were declared, but the policy hard-coded ``network_mode:
"none"`` and the client always used ``docker.from_env()``.
"""

from unittest.mock import MagicMock

import core.config.sandbox as sandbox_config
from core.config.sandbox import SandboxConfig
from core.services.sandbox import docker_factory
from core.services.sandbox.policy import build_sandbox_runtime_kwargs


def _use(monkeypatch, **fields) -> None:
    monkeypatch.setattr(sandbox_config, "_sandbox_config", SandboxConfig(**fields))


def test_network_stays_off_by_default(monkeypatch):
    _use(monkeypatch)
    assert build_sandbox_runtime_kwargs()["network_mode"] == "none"


def test_network_opt_in_uses_the_bridge(monkeypatch):
    _use(monkeypatch, enable_network=True)
    kwargs = build_sandbox_runtime_kwargs()
    assert kwargs["network_mode"] == "bridge"
    # Opting into egress relaxes nothing else.
    assert kwargs["cap_drop"] == ["ALL"] and kwargs["read_only"] is True


def test_explicit_argument_wins(monkeypatch):
    _use(monkeypatch, enable_network=True)
    assert build_sandbox_runtime_kwargs(enable_network=False)["network_mode"] == "none"


def test_default_socket_keeps_docker_environment(monkeypatch):
    _use(monkeypatch)
    fake = MagicMock()
    monkeypatch.setattr(docker_factory, "docker", fake)
    docker_factory._connect()
    fake.from_env.assert_called_once()
    fake.DockerClient.assert_not_called()


def test_explicit_socket_is_used(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    _use(monkeypatch, docker_socket="/run/user/1000/docker.sock")
    fake = MagicMock()
    monkeypatch.setattr(docker_factory, "docker", fake)
    docker_factory._connect()
    fake.DockerClient.assert_called_once_with(
        base_url="unix:///run/user/1000/docker.sock"
    )


def test_docker_host_beats_the_socket(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://remote:2376")
    _use(monkeypatch, docker_socket="/run/user/1000/docker.sock")
    fake = MagicMock()
    monkeypatch.setattr(docker_factory, "docker", fake)
    docker_factory._connect()
    fake.from_env.assert_called_once()
