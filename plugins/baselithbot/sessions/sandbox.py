"""Docker per-session sandbox controller.

Provides isolated execution environments for non-primary sessions, in line
with the OpenClaw "per-session Docker sandboxing" guarantee. The Docker
daemon must be reachable; if it is not, ``SandboxError`` is raised at
``startup``. Containers are tagged with the session id for traceability.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess  # nosec B404 - argv list, shell=False
from typing import Any


class SandboxError(RuntimeError):
    """Raised when sandbox provisioning or command execution fails."""


class DockerSandbox:
    """Manage a long-lived sandbox container per session."""

    def __init__(
        self,
        session_id: str,
        image: str = "python:3.12-slim",
        network: str = "none",
        memory: str = "512m",
        cpus: str = "1.0",
        workdir: str = "/workspace",
    ) -> None:
        self.session_id = session_id
        self.image = image
        self.network = network
        self.memory = memory
        self.cpus = cpus
        self.workdir = workdir
        self._container: str | None = None

    @staticmethod
    async def _invoke(argv: list[str], timeout: float = 30.0) -> dict[str, Any]:
        def _go() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # nosec B603
                argv, shell=False, capture_output=True, timeout=timeout, check=False
            )

        try:
            completed = await asyncio.to_thread(_go)
        except subprocess.TimeoutExpired as err:
            raise SandboxError(f"timeout after {timeout}s") from err
        return {
            "return_code": completed.returncode,
            "stdout": completed.stdout.decode("utf-8", "replace"),
            "stderr": completed.stderr.decode("utf-8", "replace"),
        }

    async def startup(self) -> None:
        result = await self._invoke(["docker", "version"], timeout=10.0)
        if result["return_code"] != 0:
            raise SandboxError("docker daemon not reachable")

        name = f"baselithbot-{self.session_id[:12]}"
        argv = [
            "docker", "run", "-d", "--rm",
            "--name", name,
            "--network", self.network,
            "--memory", self.memory,
            "--cpus", self.cpus,
            "--workdir", self.workdir,
            self.image,
            "sleep", "infinity",
        ]
        result = await self._invoke(argv, timeout=60.0)
        if result["return_code"] != 0:
            raise SandboxError(f"docker run failed: {result['stderr'][:200]}")
        self._container = name

    async def run_in_container(self, command: str, timeout: float = 60.0) -> dict[str, Any]:
        if not self._container:
            raise SandboxError("sandbox not started")
        argv = ["docker", "exec", self._container, "sh", "-c", command]
        return await self._invoke(argv, timeout=timeout)

    async def shutdown(self) -> None:
        if not self._container:
            return
        await self._invoke(["docker", "kill", self._container], timeout=15.0)
        self._container = None

    @property
    def container_name(self) -> str | None:
        return self._container

    def __repr__(self) -> str:
        return (
            f"DockerSandbox(session={self.session_id!r}, "
            f"image={self.image!r}, container={self._container!r})"
        )


def parse_resource_spec(spec: str) -> list[str]:
    """Helper for converting `'--cpus 1 --memory 256m'` into argv tokens."""
    return shlex.split(spec)


__all__ = ["DockerSandbox", "SandboxError", "parse_resource_spec"]
