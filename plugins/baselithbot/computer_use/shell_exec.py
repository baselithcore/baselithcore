"""Sandboxed subprocess execution with command allowlist.

The Computer Use shell tool is opt-in (``allow_shell=True``) and matches
the *first token* of every command against ``allowed_shell_commands``
(exact match or prefix-with-slash). Subprocess is invoked with
``shell=False`` (argument vector, never a string) and a hard timeout.
stdout/stderr are truncated to keep LLM context small.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess  # nosec B404 - shell=False used everywhere
from typing import Any

from plugins.baselithbot.computer_use.config import AuditLogger, ComputerUseConfig, ComputerUseError
from plugins.baselithbot.control.approvals import ApprovalGate, ApprovalStatus

_MAX_OUTPUT_BYTES = 65536

# Tokens that imply shell-level composition (pipes, redirects, chaining,
# command substitution). With ``shell=False`` these never do what the caller
# expects — the child process just receives them as literal argv tokens,
# which confuses LLM-driven agents into retry loops on meaningless errors
# like ``ifconfig: interface | does not exist``. Reject early with a clear
# message so the planner can pick a single-binary alternative.
_SHELL_META_TOKENS = frozenset({"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "(", ")", "$("})
_SHELL_META_SUBSTRINGS = ("`", "\n")


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
            raise ComputerUseError(f"operator {req.status.value} shell_run (approval id={req.id})")

    def _check_no_shell_meta(self, command: str) -> None:
        # Re-lex with punctuation_chars=True so shell operators become their
        # own tokens while quoted content (e.g. grep regex ``'foo|bar'``)
        # stays intact. This avoids false positives on legitimately quoted
        # arguments while still catching ``cmd | other`` / ``cmd ; other``
        # where shlex.split leaves ``cmd;`` glued.
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        for token in lexer:
            if token in _SHELL_META_TOKENS or any(s in token for s in _SHELL_META_SUBSTRINGS):
                raise ComputerUseError(
                    f"shell metacharacter {token!r} is not supported "
                    "(subprocess runs with shell=False — no pipes, redirects, "
                    "chaining, or command substitution); split the work into "
                    "separate single-binary calls"
                )

    def _check_allowed(self, argv: list[str]) -> None:
        if not argv:
            raise ComputerUseError("empty command")
        head = argv[0]
        allowlist = self._config.allowed_shell_commands
        if not allowlist:
            raise ComputerUseError(
                "no shell commands are allowlisted; set computer_use.allowed_shell_commands"
            )
        # Tight matching rules:
        # - Absolute-path pattern (``/usr/bin/ls``) → match head exactly.
        # - Bare-name pattern (``ls``) → only match a bare-name head. This
        #   blocks attacker-controlled paths like ``/tmp/pwn/ls`` that used
        #   to slip past an ``endswith("/" + pattern)`` check just because
        #   the final path component matched a listed binary.
        head_is_path = os.sep in head or head.startswith("./") or head.startswith("../")
        for pattern in allowlist:
            if os.path.isabs(pattern):
                if head == pattern:
                    return
                continue
            if not head_is_path and head == pattern:
                return
        raise ComputerUseError(f"command '{head}' is not in the allowlist")

    async def run(self, command: str, cwd: str | None = None) -> dict[str, Any]:
        """Execute a command string parsed via ``shlex``."""
        self._config.require_enabled("shell")

        self._check_no_shell_meta(command)
        argv = shlex.split(command)
        self._check_allowed(argv)
        await self._gate(argv, cwd)

        def _invoke() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # noqa: S603 - argv is allowlisted via _check_allowed, shell=False
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
