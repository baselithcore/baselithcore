"""
Shared Docker runtime policy for sandboxed code execution.
"""

from __future__ import annotations

from typing import Any


def build_sandbox_runtime_kwargs() -> dict[str, Any]:
    """Return conservative Docker runtime options for untrusted code."""

    return {
        "network_mode": "none",
        "mem_limit": "128m",
        "cpu_period": 100000,
        "cpu_quota": 50000,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "pids_limit": 64,
        "tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
    }
