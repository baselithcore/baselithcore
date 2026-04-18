"""Sandboxed subprocess execution with command allowlist.

The Computer Use shell tool is opt-in (``allow_shell=True``) and matches
the *first token* of every command against ``allowed_shell_commands``
(exact match or prefix-with-slash). Subprocess is invoked with
``shell=False`` (argument vector, never a string) and a hard timeout.
stdout/stderr are truncated to keep LLM context small.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess  # nosec B404 - shell=False used everywhere
from typing import Any

from .approvals import ApprovalGate, ApprovalStatus
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError

_MAX_OUTPUT_BYTES = 65536


class ShellExecutor:
    """Run shell commands matching the configured allowlist."""

    def __init__(
        self,
        config: ComputerUseConfig,
        audit: AuditLogger,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._config = config
        self._audit = audit
        self._approvals = approvals

    async def _gate(self, argv: list[str], cwd: str | None) -> None:
        if self._approvals is None:
            return
        if "shell" not in self._config.require_approval_for:
            return
        req = await self._approvals.submit(
            capability="shell",
            action="shell_run",
            params={"argv": argv, "cwd": cwd},
            timeout_seconds=self._config.approval_timeout_seconds,
        )
        if req.status != ApprovalStatus.APPROVED:
            self._audit.record(
                f"shell_run.{req.status.value}",
                argv=argv,
                cwd=cwd,
                status=req.status.value,
                approval_id=req.id,
            )
            raise ComputerUseError(
                f"operator {req.status.value} shell_run (approval id={req.id})"
            )

    def _check_allowed(self, argv: list[str]) -> None:
        if not argv:
            raise ComputerUseError("empty command")
        head = argv[0]
        allowlist = self._config.allowed_shell_commands
        if not allowlist:
            raise ComputerUseError(
                "no shell commands are allowlisted; "
                "set computer_use.allowed_shell_commands"
            )
        for pattern in allowlist:
            if head == pattern or head.endswith("/" + pattern):
                return
        raise ComputerUseError(f"command '{head}' is not in the allowlist")

    async def run(self, command: str, cwd: str | None = None) -> dict[str, Any]:
        """Execute a command string parsed via ``shlex``."""
        self._config.require_enabled("shell")

        argv = shlex.split(command)
        self._check_allowed(argv)
        await self._gate(argv, cwd)

        def _invoke() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # nosec B603 - argv is allowlisted, shell=False
                argv,
                shell=False,
                capture_output=True,
                cwd=cwd,
                timeout=self._config.shell_timeout_seconds,
                check=False,
            )

        try:
            completed = await asyncio.to_thread(_invoke)
        except subprocess.TimeoutExpired:
            self._audit.record("shell_timeout", argv=argv, cwd=cwd)
            raise ComputerUseError(
                f"command timed out after {self._config.shell_timeout_seconds}s"
            ) from None

        stdout_b: bytes = completed.stdout or b""
        stderr_b: bytes = completed.stderr or b""
        stdout = stdout_b[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        stderr = stderr_b[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        self._audit.record(
            "shell_run",
            argv=argv,
            cwd=cwd,
            return_code=completed.returncode,
            stdout_bytes=len(stdout_b),
            stderr_bytes=len(stderr_b),
        )
        return {
            "return_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "argv": argv,
        }


__all__ = ["ShellExecutor"]
