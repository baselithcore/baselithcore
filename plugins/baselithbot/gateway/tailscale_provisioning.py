"""Tailscale provisioning helpers (``tailscale up`` / ``down`` / ``logout``)."""

from __future__ import annotations

import asyncio
import shutil
import subprocess  # nosec B404 - argv list, shell=False
from typing import Any


async def _invoke(argv: list[str], timeout: float = 60.0) -> dict[str, Any]:
    binary = shutil.which("tailscale")
    if binary is None:
        return {"status": "unavailable", "error": "tailscale CLI not installed"}
    real = [binary, *argv[1:]]

    def _go() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(  # nosec B603
            real, shell=False, capture_output=True, timeout=timeout, check=False
        )

    try:
        completed = await asyncio.to_thread(_go)
    except subprocess.TimeoutExpired:
        return {"status": "error", "error": f"timed out after {timeout}s"}
    return {
        "status": "success" if completed.returncode == 0 else "failed",
        "return_code": completed.returncode,
        "stdout": completed.stdout.decode("utf-8", "replace")[:2000],
        "stderr": completed.stderr.decode("utf-8", "replace")[:2000],
    }


class TailscaleProvisioner:
    """Wrap the local ``tailscale`` CLI for opt-in network provisioning."""

    @staticmethod
    async def up(
        auth_key: str | None = None,
        ssh: bool = False,
        accept_routes: bool = False,
        hostname: str | None = None,
    ) -> dict[str, Any]:
        argv = ["tailscale", "up"]
        if auth_key:
            argv += ["--auth-key", auth_key]
        if ssh:
            argv.append("--ssh")
        if accept_routes:
            argv.append("--accept-routes")
        if hostname:
            argv += ["--hostname", hostname]
        return await _invoke(argv, timeout=120.0)

    @staticmethod
    async def down() -> dict[str, Any]:
        return await _invoke(["tailscale", "down"], timeout=30.0)

    @staticmethod
    async def logout() -> dict[str, Any]:
        return await _invoke(["tailscale", "logout"], timeout=30.0)


__all__ = ["TailscaleProvisioner"]
