"""Health-check / doctor diagnostic surface for the Baselithbot plugin."""

from __future__ import annotations

import importlib.util
import platform
import shutil
import sys
from typing import Any


def _probe_dep(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


async def run_doctor() -> dict[str, Any]:
    """Return a structured environment + capability report."""
    deps = {
        "playwright": _probe_dep("playwright"),
        "playwright_stealth": _probe_dep("playwright_stealth"),
        "pyautogui": _probe_dep("pyautogui"),
        "mss": _probe_dep("mss"),
        "PIL": _probe_dep("PIL"),
        "httpx": _probe_dep("httpx"),
        "google.cloud.pubsub_v1": _probe_dep("google.cloud.pubsub_v1"),
        "apscheduler": _probe_dep("apscheduler"),
    }
    binaries = {
        binary: bool(shutil.which(binary))
        for binary in ("docker", "ssh", "tailscale", "say", "espeak")
    }
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split(" ")[0],
        },
        "python_dependencies": deps,
        "system_binaries": binaries,
    }


__all__ = ["run_doctor"]
