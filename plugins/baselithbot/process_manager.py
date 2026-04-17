"""Process listing + termination, gated by ``ComputerUseConfig.allow_shell``."""

from __future__ import annotations

import asyncio
import os
import signal
from typing import Any

try:
    import psutil  # type: ignore[import-not-found]

    _HAS_PSUTIL = True
except ImportError:
    psutil = None  # type: ignore[assignment]
    _HAS_PSUTIL = False

from .computer_use import AuditLogger, ComputerUseConfig


class ProcessManager:
    """List + kill processes; requires opt-in shell capability."""

    def __init__(self, config: ComputerUseConfig, audit: AuditLogger) -> None:
        self._config = config
        self._audit = audit

    async def list_processes(self, limit: int = 200) -> list[dict[str, Any]]:
        self._config.require_enabled("shell")
        if not _HAS_PSUTIL or psutil is None:
            raise RuntimeError("psutil not installed; pip install psutil")
        ps = psutil

        def _scan() -> list[dict[str, Any]]:
            entries: list[dict[str, Any]] = []
            for proc in ps.process_iter(
                attrs=("pid", "name", "username", "cpu_percent", "memory_percent")
            ):
                try:
                    info = proc.info
                except (ps.NoSuchProcess, ps.AccessDenied):
                    continue
                entries.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "user": info.get("username"),
                        "cpu_percent": info.get("cpu_percent"),
                        "memory_percent": info.get("memory_percent"),
                    }
                )
                if len(entries) >= limit:
                    break
            return entries

        entries = await asyncio.to_thread(_scan)
        self._audit.record("process_list", count=len(entries))
        return entries

    async def kill(self, pid: int, sig: int = signal.SIGTERM) -> dict[str, Any]:
        self._config.require_enabled("shell")
        try:
            os.kill(pid, sig)
            self._audit.record("process_kill", pid=pid, signal=sig)
            return {"status": "success", "pid": pid, "signal": sig}
        except ProcessLookupError:
            return {"status": "error", "error": "no such process", "pid": pid}
        except PermissionError as exc:
            return {"status": "error", "error": str(exc), "pid": pid}


__all__ = ["ProcessManager"]
