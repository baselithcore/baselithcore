"""Tailscale integration surface (status query + advise)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess  # nosec B404 - argv list, shell=False
from typing import Any

from pydantic import BaseModel


class TailscaleStatus(BaseModel):
    installed: bool
    logged_in: bool = False
    self_ip: str | None = None
    peers: list[dict[str, Any]] = []
    error: str | None = None


class TailscaleGateway:
    """Query the local ``tailscale`` CLI for connectivity status."""

    @staticmethod
    async def status() -> TailscaleStatus:
        binary = shutil.which("tailscale")
        if binary is None:
            return TailscaleStatus(installed=False, error="tailscale CLI not installed")

        def _go() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(  # noqa: S603 - binary resolved via shutil.which, argv list, shell=False
                [binary, "status", "--json"],
                shell=False,
                capture_output=True,
                timeout=10.0,
                check=False,
            )

        try:
            completed = await asyncio.to_thread(_go)
        except subprocess.TimeoutExpired:
            return TailscaleStatus(installed=True, error="tailscale status timed out")

        if completed.returncode != 0:
            return TailscaleStatus(
                installed=True,
                error=completed.stderr.decode("utf-8", "replace")[:500],
            )

        try:
            payload = json.loads(completed.stdout.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            return TailscaleStatus(installed=True, error=f"invalid JSON: {exc}")

        self_block = payload.get("Self", {}) or {}
        peers_dict = payload.get("Peer", {}) or {}
        peers = [
            {
                "hostname": p.get("HostName"),
                "online": p.get("Online"),
                "address": (p.get("TailscaleIPs") or [None])[0],
            }
            for p in peers_dict.values()
        ]
        return TailscaleStatus(
            installed=True,
            logged_in=bool(self_block.get("Online", False)),
            self_ip=(self_block.get("TailscaleIPs") or [None])[0],
            peers=peers,
        )


__all__ = ["TailscaleGateway", "TailscaleStatus"]
