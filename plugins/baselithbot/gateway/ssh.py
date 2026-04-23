"""Remote gateway control over SSH.

Wraps an SSH command-execution surface. Concrete connection layer is left
to the operator (preferred: pre-installed ``ssh`` binary with key-based
auth). Commands flow through ``ShellExecutor``-style allowlists, so
operators must explicitly opt every remote command in.

``extra_options`` is strictly validated to block OpenSSH directives that
can execute arbitrary local code (``ProxyCommand``, ``LocalCommand`` with
``PermitLocalCommand=yes``, ``KnownHostsCommand``, ``Match exec``, etc.)
or load operator-controlled config (``-F``, ``Include``). Anything not on
the explicit allowlist is refused at config-load time.
"""

from __future__ import annotations

import asyncio
import shlex
import subprocess  # nosec B404 - argv list, shell=False
from typing import Any

from pydantic import BaseModel, Field, field_validator

# OpenSSH -o keys considered safe: do not exec arbitrary code, do not
# reload config, do not enable port forwarding surprises.
_SAFE_SSH_O_KEYS: frozenset[str] = frozenset(
    {
        "batchmode",
        "ciphers",
        "compression",
        "connectionattempts",
        "connecttimeout",
        "hostkeyalgorithms",
        "identitiesonly",
        "kexalgorithms",
        "loglevel",
        "macs",
        "passwordauthentication",
        "preferredauthentications",
        "pubkeyauthentication",
        "serveralivecountmax",
        "serveraliveinterval",
        "stricthostkeychecking",
        "userknownhostsfile",
    }
)

# Short flags that cannot execute code or broaden the attack surface.
_SAFE_SSH_SHORT_FLAGS: frozenset[str] = frozenset(
    {"-4", "-6", "-A", "-C", "-q", "-T", "-v", "-vv", "-vvv"}
)


def _validate_extra_option(opt: str) -> str:
    """Return ``opt`` if it passes the SSH allowlist; raise otherwise."""
    if not isinstance(opt, str) or not opt:
        raise ValueError("ssh extra_option must be a non-empty string")
    if opt in _SAFE_SSH_SHORT_FLAGS:
        return opt
    if opt.startswith("-o") or opt == "-o":
        # Accept both "-oKey=Val" and a single combined "-o" with Key=Val.
        body = opt[2:] if opt.startswith("-o") and len(opt) > 2 else ""
        if not body:
            raise ValueError(
                "ssh extra_option '-o' requires 'Key=Value' inline (e.g. '-oBatchMode=yes')"
            )
        key, sep, _ = body.partition("=")
        if not sep:
            raise ValueError(f"ssh extra_option '{opt}' must be 'Key=Value'")
        if key.lower() not in _SAFE_SSH_O_KEYS:
            raise ValueError(
                f"ssh extra_option '-o{key}' is not allowlisted "
                f"(blocked: ProxyCommand/LocalCommand/KnownHostsCommand/Match/Include)"
            )
        return opt
    raise ValueError(
        f"ssh extra_option '{opt}' is not allowlisted; "
        "allowed: short flags from the safe set or '-oKey=Value' with allowlisted key"
    )


class SSHGatewayConfig(BaseModel):
    host: str
    user: str | None = None
    port: int = 22
    key_path: str | None = None
    extra_options: list[str] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)
    timeout_seconds: float = 60.0

    @field_validator("extra_options")
    @classmethod
    def _check_extra_options(cls, value: list[str]) -> list[str]:
        return [_validate_extra_option(v) for v in value]


class SSHGateway:
    """Send allowlisted commands to a remote host via the ``ssh`` binary."""

    def __init__(self, config: SSHGatewayConfig) -> None:
        self._config = config

    def _check(self, command: str) -> None:
        if not self._config.allowed_commands:
            raise PermissionError("SSH allowed_commands is empty; refusing to run")
        head = shlex.split(command)[0] if command else ""
        if head not in self._config.allowed_commands:
            raise PermissionError(f"command '{head}' is not in SSH allowed_commands")

    async def run(self, command: str) -> dict[str, Any]:
        self._check(command)
        target = (
            f"{self._config.user}@{self._config.host}" if self._config.user else self._config.host
        )
        argv = ["ssh", "-p", str(self._config.port)]
        if self._config.key_path:
            argv += ["-i", self._config.key_path]
        argv += list(self._config.extra_options)
        argv += [target, command]

        def _go() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # noqa: S603 - argv list built internally, shell=False
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
