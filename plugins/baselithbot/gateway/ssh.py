"""Remote gateway control over SSH.

Wraps an SSH command-execution surface. Concrete connection layer is left
to the operator (preferred: pre-installed ``ssh`` binary with key-based
auth). Commands flow through ``ShellExecutor``-style allowlists, so
operators must explicitly opt every remote command in.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess  # nosec B404 - argv list, shell=False
from typing import Any

from pydantic import BaseModel, Field


class SSHGatewayConfig(BaseModel):
    host: str
    user: str | None = None
    port: int = 22
    key_path: str | None = None
    extra_options: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    timeout_seconds: float = 60.0


class SSHGateway:
    """Send allowlisted commands to a remote host via the ``ssh`` binary."""

    def __init__(self, config: SSHGatewayConfig) -> None:
        self._config = config

    def _check(self, command: str) -> None:
        if not self._config.allowed_commands:
            raise PermissionError("SSH allowed_commands is empty; refusing to run")
        head = shlex.split(command)[0] if command else ""
        if head not in self._config.allowed_commands:
            raise PermissionError(
                f"command '{head}' is not in SSH allowed_commands"
            )

    async def run(self, command: str) -> dict[str, Any]:
        self._check(command)
        target = (
            f"{self._config.user}@{self._config.host}"
            if self._config.user
            else self._config.host
        )
        argv = ["ssh", "-p", str(self._config.port)]
        if self._config.key_path:
            argv += ["-i", self._config.key_path]
        argv += list(self._config.extra_options)
        argv += [target, command]

        def _go() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # nosec B603
                argv,
                shell=False,
                capture_output=True,
                timeout=self._config.timeout_seconds,
                check=False,
            )

        completed = await asyncio.to_thread(_go)
        return {
            "return_code": completed.returncode,
            "stdout": completed.stdout.decode("utf-8", "replace"),
            "stderr": completed.stderr.decode("utf-8", "replace"),
            "argv": argv,
        }


__all__ = ["SSHGateway", "SSHGatewayConfig"]
