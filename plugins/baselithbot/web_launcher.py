"""Web launcher helper — deterministic URL open via OS default handler.

Exposes a typed entry point for the desktop agent to launch web URLs
without resorting to keyboard shortcuts (`cmd+l`, type, enter) or mouse
clicks on the location bar. On macOS this delegates to the ``open``
binary, which focuses the user's default browser and navigates the new
tab to the target URL in a single deterministic call.

Security model:
    - Gated on ``ComputerUseConfig.allow_shell`` (same risk class as any
      subprocess call).
    - Requires ``open`` to be in ``allowed_shell_commands`` so the
      operator has explicitly consented to arbitrary URL launches.
    - The URL is validated against a conservative scheme allowlist
      (``http``, ``https``, ``mailto``, ``tel``) — deep-link / custom
      schemes (e.g. ``file://``, ``javascript:``) are rejected so the
      agent cannot leak local files or evaluate scripts.
"""

from __future__ import annotations

import asyncio
import subprocess  # nosec B404 - argv-only invocation, shell=False
import sys
from typing import Any
from urllib.parse import urlparse

from .approvals import ApprovalGate, ApprovalStatus
from .computer_use import AuditLogger, ComputerUseConfig, ComputerUseError

_ALLOWED_SCHEMES = frozenset({"http", "https", "mailto", "tel"})


class WebLauncher:
    """Deterministic URL launcher via ``open`` (macOS) / ``xdg-open`` (Linux)."""

    def __init__(
        self,
        config: ComputerUseConfig,
        audit: AuditLogger,
        approvals: ApprovalGate | None = None,
    ) -> None:
        self._config = config
        self._audit = audit
        self._approvals = approvals

    def _binary(self) -> str:
        if sys.platform == "darwin":
            return "open"
        if sys.platform.startswith("linux"):
            return "xdg-open"
        raise ComputerUseError(
            "web launcher only supports macOS ('open') and Linux ('xdg-open'); "
            f"current platform is {sys.platform!r}"
        )

    def _require_binary_allowlisted(self, binary: str) -> None:
        if binary not in self._config.allowed_shell_commands:
            raise ComputerUseError(
                f"{binary!r} is not in computer_use.allowed_shell_commands; "
                f"add {binary!r} to the Shell allowlist before launching URLs"
            )

    def _validate_url(self, url: str) -> str:
        trimmed = url.strip()
        if not trimmed:
            raise ComputerUseError("url is empty")
        if len(trimmed) > 2048:
            raise ComputerUseError("url exceeds 2048 chars")
        parsed = urlparse(trimmed)
        scheme = parsed.scheme.lower()
        if scheme not in _ALLOWED_SCHEMES:
            raise ComputerUseError(
                f"scheme {scheme!r} not allowed; accepted: "
                f"{sorted(_ALLOWED_SCHEMES)}"
            )
        if scheme in {"http", "https"} and not parsed.netloc:
            raise ComputerUseError("http(s) url must have a host")
        return trimmed

    async def _gate(self, url: str, binary: str) -> None:
        if self._approvals is None:
            return
        if "shell" not in self._config.require_approval_for:
            return
        req = await self._approvals.submit(
            capability="shell",
            action="web_open",
            params={"url": url, "binary": binary},
            timeout_seconds=self._config.approval_timeout_seconds,
        )
        if req.status != ApprovalStatus.APPROVED:
            self._audit.record(
                f"web_open.{req.status.value}",
                url=url,
                status=req.status.value,
                approval_id=req.id,
            )
            raise ComputerUseError(
                f"operator {req.status.value} web_open (approval id={req.id})"
            )

    async def open_url(self, url: str) -> dict[str, Any]:
        """Launch ``url`` in the OS-default handler."""
        self._config.require_enabled("shell")
        binary = self._binary()
        self._require_binary_allowlisted(binary)
        safe_url = self._validate_url(url)
        await self._gate(safe_url, binary)

        argv = [binary, safe_url]

        def _invoke() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # nosec B603 - argv vector, shell=False
                argv,
                shell=False,
                capture_output=True,
                timeout=self._config.shell_timeout_seconds,
                check=False,
            )

        try:
            completed = await asyncio.to_thread(_invoke)
        except subprocess.TimeoutExpired as exc:
            self._audit.record("web_open.timeout", url=safe_url)
            raise ComputerUseError(
                f"web_open timed out after {self._config.shell_timeout_seconds}s"
            ) from exc

        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        self._audit.record(
            "web_open",
            url=safe_url,
            return_code=completed.returncode,
            stderr_bytes=len(stderr),
        )
        if completed.returncode != 0:
            raise ComputerUseError(
                f"{binary} exit {completed.returncode}: {stderr[:200]}"
            )
        return {"url": safe_url, "binary": binary, "return_code": 0}


__all__ = ["WebLauncher"]
