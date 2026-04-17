"""Health-check / doctor diagnostic surface for the Baselithbot plugin."""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .plugin import BaselithbotPlugin


_PYTHON_DEPS: tuple[str, ...] = (
    "playwright",
    "playwright_stealth",
    "pyautogui",
    "mss",
    "PIL",
    "httpx",
    "google.cloud.pubsub_v1",
    "apscheduler",
    "openai",
    "anthropic",
    "google.generativeai",
    "groq",
    "cryptography",
)

_SYSTEM_BINARIES: tuple[str, ...] = (
    "docker",
    "ssh",
    "tailscale",
    "say",
    "espeak",
    "ffmpeg",
    "git",
)


def _probe_dep(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ValueError, ModuleNotFoundError):
        return False


def _path_info(path: Path) -> dict[str, Any]:
    exists = path.exists()
    kind = "missing"
    size_bytes: int | None = None
    if exists:
        if path.is_dir():
            kind = "dir"
        elif path.is_file():
            kind = "file"
            try:
                size_bytes = path.stat().st_size
            except OSError:
                size_bytes = None
        else:
            kind = "other"
    return {
        "path": str(path),
        "exists": exists,
        "kind": kind,
        "size_bytes": size_bytes,
    }


def _plugin_runtime(plugin: "BaselithbotPlugin") -> dict[str, Any]:
    agent = plugin.agent
    agent_info: dict[str, Any] = {
        "state": agent.state.value if agent is not None else "uninitialized",
        "backend_started": bool(agent is not None and agent._backend is not None),
        "stealth_enabled": bool(agent is not None and agent.stealth_config.enabled),
    }

    cron = plugin.cron
    cron_info: dict[str, Any] = {
        "backend": cron.backend,
        "running": cron.running,
        "jobs": len(cron.list()),
        "custom_jobs": len(plugin.custom_crons.list()),
    }

    channels = plugin.channels
    live_instances = getattr(channels, "_instances", {})
    channels_info: dict[str, Any] = {
        "known": len(channels.known()),
        "live": len(live_instances),
    }

    agents_entries = plugin.agent_registry.list()
    custom_names = {spec.name for spec in plugin.custom_agents.list()}
    agents_info: dict[str, Any] = {
        "total": len(agents_entries),
        "system": sum(1 for e in agents_entries if e.name not in custom_names),
        "custom": len(custom_names),
    }

    providers = plugin.secret_store.snapshot()
    provider_info: dict[str, Any] = {
        "total": len(providers),
        "configured": sum(1 for p in providers if p.get("configured")),
    }

    return {
        "agent": agent_info,
        "cron": cron_info,
        "channels": channels_info,
        "sessions": {"count": len(plugin.sessions.list())},
        "skills": {"count": len(plugin.skills.list())},
        "workspaces": {"count": len(plugin.workspaces.list())},
        "agents": agents_info,
        "provider_keys": provider_info,
        "nodes": {"paired": len(plugin.pairing.list_paired())},
        "canvas": {"widgets": len(plugin.canvas.widgets)},
        "usage": plugin.usage.summary(),
        "inbound": plugin.inbound_dispatcher.stats(),
    }


def _state_paths(plugin: "BaselithbotPlugin") -> dict[str, dict[str, Any]]:
    root = Path(plugin._state_dir)
    writable = False
    try:
        writable = os.access(root, os.W_OK)
    except OSError:
        writable = False
    out: dict[str, dict[str, Any]] = {
        "state_dir": {**_path_info(root), "writable": writable},
        "workspaces": _path_info(root / "workspaces.json"),
        "custom_crons": _path_info(root / "custom_crons.json"),
        "custom_agents": _path_info(root / "custom_agents.json"),
        "provider_keys": _path_info(root / "provider_keys.enc.json"),
        "clawhub": _path_info(root / "clawhub"),
    }
    return out


async def run_doctor(
    plugin: "BaselithbotPlugin | None" = None,
) -> dict[str, Any]:
    """Return a structured environment + capability report.

    When ``plugin`` is provided the report includes a live ``plugin_runtime``
    snapshot (agent/cron/channels/… counts) and ``state_paths`` verifying the
    on-disk stores used by the dashboard.
    """
    report: dict[str, Any] = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": sys.version.split(" ")[0],
        },
        "python_dependencies": {m: _probe_dep(m) for m in _PYTHON_DEPS},
        "system_binaries": {b: bool(shutil.which(b)) for b in _SYSTEM_BINARIES},
    }
    if plugin is not None:
        report["plugin_runtime"] = _plugin_runtime(plugin)
        report["state_paths"] = _state_paths(plugin)
    return report


__all__ = ["run_doctor"]
