"""Bounded per-call sandbox resources.

A caller may ask for more memory, scratch space or time than the defaults, but
only up to operator-set ceilings (``SANDBOX_MAX_*``). The ceilings default to
today's hard-coded values, so nothing changes until an operator raises them.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

import baselith.core.config.sandbox as sandbox_config
from baselith.core.config.sandbox import SandboxConfig
from baselith.core.services.sandbox import Resources
from baselith.core.services.sandbox.policy import build_sandbox_runtime_kwargs
from baselith.core.services.sandbox.resources import clamp, effective_timeout
from baselith.core.services.sandbox.service import SandboxService

_RAISED = {"max_memory_mb": 2048, "max_tmpfs_mb": 1024, "max_timeout": 300}


def _use(monkeypatch, **fields) -> SandboxConfig:
    config = SandboxConfig(**fields)
    monkeypatch.setattr(sandbox_config, "_sandbox_config", config)
    return config


def test_ceilings_default_to_todays_hard_coded_values():
    config = SandboxConfig()

    assert (config.max_memory_mb, config.max_tmpfs_mb, config.max_timeout) == (
        128,
        64,
        30,
    )


def test_ceilings_read_their_environment_variables(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_MEMORY_MB", "512")
    monkeypatch.setenv("SANDBOX_MAX_TMPFS_MB", "256")
    monkeypatch.setenv("SANDBOX_MAX_TIMEOUT", "90")

    config = SandboxConfig()

    assert (config.max_memory_mb, config.max_tmpfs_mb, config.max_timeout) == (
        512,
        256,
        90,
    )


@pytest.mark.parametrize(
    "field, bad",
    [
        ("max_memory_mb", 63),
        ("max_memory_mb", 16385),
        ("max_tmpfs_mb", 15),
        ("max_tmpfs_mb", 8193),
        ("max_timeout", 0),
        ("max_timeout", 3601),
    ],
)
def test_ceilings_are_range_checked(field, bad):
    with pytest.raises(ValidationError):
        SandboxConfig(**{field: bad})


def test_no_request_gets_the_defaults():
    assert clamp(None, SandboxConfig()) == (128, 64, 30)
    assert clamp(Resources(), SandboxConfig()) == (128, 64, 30)


def test_ceilings_win_over_a_larger_request():
    assert clamp(Resources(1024, 512, 120), SandboxConfig()) == (128, 64, 30)


def test_raised_ceilings_let_the_request_through():
    config = SandboxConfig(**_RAISED)

    assert clamp(Resources(1024, 512, 120), config) == (1024, 512, 120)


def test_a_request_above_a_raised_ceiling_is_still_clamped():
    config = SandboxConfig(**_RAISED)

    assert clamp(Resources(4096, 2048, 900), config) == (2048, 1024, 300)


def test_a_smaller_request_than_the_default_is_honoured():
    config = SandboxConfig(**_RAISED)

    assert clamp(Resources(96, 32, 5), config) == (96, 32, 5)


def test_unset_fields_fall_back_to_the_defaults_independently():
    config = SandboxConfig(**_RAISED)

    assert clamp(Resources(memory_mb=1024), config) == (1024, 64, 30)
    assert clamp(Resources(tmpfs_mb=512), config) == (128, 512, 30)
    assert clamp(Resources(timeout_s=120), config) == (128, 64, 120)


def test_resources_are_immutable():
    with pytest.raises(AttributeError):
        Resources().memory_mb = 1  # type: ignore[misc]


def test_runtime_kwargs_are_unchanged_without_resources(monkeypatch):
    _use(monkeypatch)
    kwargs = build_sandbox_runtime_kwargs()

    assert kwargs["mem_limit"] == "128m"
    assert kwargs["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=64m"}
    assert kwargs == build_sandbox_runtime_kwargs(resources=None)


def test_runtime_kwargs_use_the_clamped_request(monkeypatch):
    _use(monkeypatch, **_RAISED)
    kwargs = build_sandbox_runtime_kwargs(resources=Resources(1024, 512, 120))

    assert kwargs["mem_limit"] == "1024m"
    assert kwargs["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=512m"}


def test_runtime_kwargs_clamp_to_the_ceiling(monkeypatch):
    _use(monkeypatch)
    kwargs = build_sandbox_runtime_kwargs(resources=Resources(8192, 4096, 600))

    assert kwargs["mem_limit"] == "128m"
    assert kwargs["tmpfs"]["/tmp"].endswith("size=64m")


def test_larger_resources_relax_nothing_else(monkeypatch):
    _use(monkeypatch, **_RAISED)
    base = build_sandbox_runtime_kwargs()
    big = build_sandbox_runtime_kwargs(resources=Resources(1024, 512, 120))

    changed = {key for key in base if base[key] != big[key]}
    assert changed == {"mem_limit", "tmpfs"}
    assert list(big["tmpfs"]) == ["/tmp"]
    assert "noexec" in big["tmpfs"]["/tmp"]


def test_effective_timeout_keeps_todays_rule_without_resources():
    config = SandboxConfig(timeout=45)

    assert effective_timeout(None, None, config) == 45
    assert effective_timeout(10, None, config) == 10
    # Today's rule is unbounded: an explicit timeout is not clamped.
    assert effective_timeout(900, None, config) == 900


def test_effective_timeout_is_clamped_once_resources_are_given():
    config = SandboxConfig(**_RAISED)

    assert effective_timeout(None, Resources(timeout_s=120), config) == 120
    assert effective_timeout(None, Resources(timeout_s=9000), config) == 300
    assert effective_timeout(900, Resources(memory_mb=256), config) == 300
    assert effective_timeout(10, Resources(memory_mb=256), config) == 10
    # The resources' own timeout wins over the positional one.
    assert effective_timeout(10, Resources(timeout_s=120), config) == 120


@pytest.fixture
def docker_run(monkeypatch):
    """A SandboxService wired to a fake docker client; returns (service, client)."""
    container = MagicMock()
    container.logs.side_effect = lambda stdout=True, stderr=True: (
        b"ok" if stdout else b""
    )
    container.wait.return_value = {"StatusCode": 0}
    with patch("baselith.core.services.sandbox.docker_factory.docker") as docker:
        client = MagicMock()
        client.containers.run.return_value = container
        docker.from_env.return_value = client
        yield SandboxService(), client, container


def test_execute_code_async_passes_the_clamped_resources(monkeypatch, docker_run):
    _use(monkeypatch, **_RAISED)
    service, client, container = docker_run

    result = asyncio.run(
        service.execute_code_async("print(1)", resources=Resources(1024, 512, 120))
    )

    assert result.exit_code == 0
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["mem_limit"] == "1024m"
    assert kwargs["tmpfs"]["/tmp"] == "rw,noexec,nosuid,size=512m"
    container.wait.assert_called_once_with(timeout=120)


def test_execute_code_async_clamps_to_the_default_ceilings(monkeypatch, docker_run):
    _use(monkeypatch)
    service, client, container = docker_run

    asyncio.run(
        service.execute_code_async("print(1)", resources=Resources(1024, 512, 120))
    )

    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["mem_limit"] == "128m"
    assert kwargs["tmpfs"]["/tmp"] == "rw,noexec,nosuid,size=64m"
    container.wait.assert_called_once_with(timeout=30)


def test_execute_code_async_without_resources_is_unchanged(monkeypatch, docker_run):
    _use(monkeypatch)
    service, client, container = docker_run

    asyncio.run(service.execute_code_async("print(1)", timeout=7))

    assert client.containers.run.call_args.kwargs == {
        "command": ["python", "-c", "print(1)"],
        "detach": True,
        "mounts": [],
        "environment": {},
        **build_sandbox_runtime_kwargs(),
    }
    container.wait.assert_called_once_with(timeout=7)
