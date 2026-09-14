"""Sandbox container hardening and fail-closed base-image resolution.

The runtime policy dropped caps and the network but left the root filesystem
writable, ran the entrypoint as uid 0 and set no ulimits, so untrusted code
could still rewrite the image layer, exhaust file descriptors or fork-bomb
within the pids cap. Separately, a missing ``Dockerfile.sandbox`` silently
degraded to a floating ``python:3.12-slim`` — an unhardened, unpinned image
substituted for the hardened one without anyone being told.
"""

import pytest

from core.config.sandbox import SandboxConfig
from core.services.sandbox import docker_factory as docker_factory_module
from core.services.sandbox.docker_factory import DockerFactory
from core.services.sandbox.policy import build_sandbox_runtime_kwargs

_MIB = 1024 * 1024


def _ulimit_map(kwargs) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for limit in kwargs["ulimits"]:
        name = getattr(limit, "name", None) or limit["Name"]
        soft = getattr(limit, "soft", None)
        hard = getattr(limit, "hard", None)
        if soft is None:
            soft, hard = limit["Soft"], limit["Hard"]
        out[name] = (soft, hard)
    return out


def test_root_filesystem_is_read_only():
    assert build_sandbox_runtime_kwargs()["read_only"] is True


def test_container_runs_as_a_non_root_user():
    assert build_sandbox_runtime_kwargs()["user"] == "65534:65534"


def test_ulimits_cap_descriptors_processes_and_file_size():
    limits = _ulimit_map(build_sandbox_runtime_kwargs())

    assert limits["nofile"] == (1024, 1024)
    assert limits["nproc"] == (256, 256)
    assert limits["fsize"] == (64 * _MIB, 64 * _MIB)


def test_working_dir_is_the_writable_tmpfs():
    """cwd must be somewhere uid 65534 can actually write.

    ``useradd -m`` on bookworm creates ``/home/sandbox`` mode 0700 owned by uid
    1000, so with ``user=65534`` the image's ``WORKDIR`` is neither readable nor
    writable — every relative-path write (and some tooling's cwd probe) fails
    with ``PermissionError``. ``/tmp`` is the one writable mount.
    """
    assert build_sandbox_runtime_kwargs()["working_dir"] == "/tmp"


def test_tmp_is_the_only_writable_mount():
    kwargs = build_sandbox_runtime_kwargs()

    assert list(kwargs["tmpfs"]) == ["/tmp"]
    assert "rw" in kwargs["tmpfs"]["/tmp"]


def test_existing_hardening_is_preserved():
    kwargs = build_sandbox_runtime_kwargs()

    assert kwargs["network_mode"] == "none"
    assert kwargs["cap_drop"] == ["ALL"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]
    assert kwargs["pids_limit"] == 64


class _MissingImage(Exception):
    """Stand-in for docker.errors.ImageNotFound."""


@pytest.fixture
def factory(monkeypatch, tmp_path):
    """A DockerFactory whose Dockerfile.sandbox is absent and client is fake."""
    monkeypatch.setattr(
        docker_factory_module, "SANDBOX_DOCKERFILE", tmp_path / "Dockerfile.sandbox"
    )
    monkeypatch.setattr(docker_factory_module, "DockerException", _MissingImage)

    factory = DockerFactory()

    class _Images:
        def __init__(self) -> None:
            self.pulled: list[str] = []

        def get(self, _name):
            raise _MissingImage("no such image")

        def pull(self, name):
            self.pulled.append(name)

    class _Client:
        def __init__(self) -> None:
            self.images = _Images()

    client = _Client()
    factory._client = client
    return factory


def _config(monkeypatch, **kwargs) -> SandboxConfig:
    config = SandboxConfig(**kwargs)
    monkeypatch.setattr(docker_factory_module, "get_sandbox_config", lambda: config)
    return config


async def test_missing_dockerfile_fails_closed(factory, monkeypatch):
    _config(monkeypatch)

    with pytest.raises(RuntimeError) as excinfo:
        await factory.ensure_image()

    message = str(excinfo.value)
    assert "Dockerfile.sandbox" in message
    assert "allow_unhardened_base" in message.lower().replace("sandbox_", "")
    assert factory._client.images.pulled == []


async def test_opt_in_without_a_digest_still_fails_closed(factory, monkeypatch):
    _config(monkeypatch, allow_unhardened_base=True)

    with pytest.raises(RuntimeError) as excinfo:
        await factory.ensure_image()

    assert "digest" in str(excinfo.value).lower()
    assert factory._client.images.pulled == []


async def test_opt_in_rejects_a_floating_tag(monkeypatch):
    with pytest.raises(ValueError):
        SandboxConfig(
            allow_unhardened_base=True, unhardened_base_image="python:3.12-slim"
        )


async def test_opt_in_pulls_the_digest_pinned_fallback(factory, monkeypatch):
    pinned = "python@sha256:" + "0" * 64
    _config(monkeypatch, allow_unhardened_base=True, unhardened_base_image=pinned)

    await factory.ensure_image()

    assert factory._client.images.pulled == [pinned]
    assert factory.base_image == pinned


def test_unhardened_base_defaults_are_closed():
    config = SandboxConfig()

    assert config.allow_unhardened_base is False
    assert config.unhardened_base_image is None
