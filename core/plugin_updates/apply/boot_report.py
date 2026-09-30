"""Per-worker boot report: which plugins each API worker actually loaded.

Every API worker writes ``boot/<pid>.json`` once its plugins are up, so the
updater can tell whether the restart it triggered really brought the new
release online. The report holds plugin names, versions, plugin directory
paths and health flags only; nothing configurable and no secrets. Reports are
written by atomic replace and never raise into the boot path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from core.config.concurrency import get_web_concurrency

from .store import RunStore, _atomic_write

logger = logging.getLogger(__name__)

_RETENTION_SECONDS = 7 * 24 * 3600


class PluginBootState(BaseModel):
    """One plugin as a worker saw it at boot."""

    model_config = ConfigDict(frozen=True)

    version: str | None
    directory: str | None
    active: bool
    healthy: bool | None = None


class BootReport(BaseModel):
    """What one worker process loaded."""

    model_config = ConfigDict(frozen=True)

    pid: int
    workers: int = 1
    booted_at: datetime
    core_version: str
    plugins: dict[str, PluginBootState]


def _plugin_name(plugin: Any) -> str | None:
    name = getattr(getattr(plugin, "metadata", None), "name", None) or getattr(
        plugin, "name", None
    )
    return name if isinstance(name, str) else None


def _healthy(result: Any, name: str) -> bool | None:
    """Read a ``check_health`` payload: ``{"plugins": {name: {"status": ..}}}``."""
    if not isinstance(result, dict):
        return None
    plugins = result.get("plugins")
    entry = plugins.get(name) if isinstance(plugins, dict) else result.get(name, result)
    if not isinstance(entry, dict):
        return None
    return bool(entry.get("healthy", entry.get("status") in ("healthy", "ok")))


async def _activate(registry: Any, name: str, budget: float) -> None:
    if budget <= 0:
        logger.warning("plugin_boot_activation_skipped plugin=%s reason=budget", name)
        return
    try:
        await asyncio.wait_for(registry.ensure_plugin_active(name), budget)
    except Exception as exc:  # reported as inactive, never raised into the boot
        logger.warning(
            "plugin_boot_activation_failed plugin=%s error=%s", name, type(exc).__name__
        )


def _resolved(directory: Path | None) -> str | None:
    """``directory`` with its links resolved; the path as given if that fails."""
    if not directory:
        return None
    try:
        return str(Path(directory).resolve())
    except (OSError, RuntimeError):  # a symlink loop: report what was registered
        return str(directory)


async def _state(
    registry: Any, name: str, active: set[str], expected: bool, budget: float
) -> PluginBootState:
    directory = registry.get_plugin_directory(name)
    healthy: bool | None = None
    if expected and name in active and budget > 0:
        try:
            healthy = _healthy(
                await asyncio.wait_for(registry.check_health(name), budget), name
            )
        except Exception as exc:
            logger.warning(
                "plugin_boot_health_failed plugin=%s error=%s", name, type(exc).__name__
            )
            healthy = False
    return PluginBootState(
        version=registry.get_plugin_version(name),
        # The loader registers the overlay link, not its target: record the
        # store entry it resolved to at boot, which is what the worker loaded.
        directory=_resolved(directory),
        active=name in active,
        healthy=healthy,
    )


def _prune(boot_dir: Path) -> None:
    cutoff = time.time() - _RETENTION_SECONDS
    for path in boot_dir.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


async def write_boot_report(
    registry: Any,
    store: RunStore,
    *,
    core_version: str,
    now: datetime | None = None,
    pid: int | None = None,
    activation_timeout: float = 30.0,
) -> Path:
    """Activate the expected plugins, then record what this worker loaded.

    ``activation_timeout`` is ONE total deadline for every activation and
    health probe. Once it is spent, remaining plugins are reported as not
    activated (``healthy=None`` when unprobed) and the report is still written.
    A timed-out activation is cancelled mid-way and can leave a plugin partly
    initialised until the next restart; the report then says ``active=False``.
    """
    deadline = time.monotonic() + activation_timeout
    expected = store.expected_plugins()
    activated_ok: set[str] = set()
    for name in expected:
        budget = deadline - time.monotonic()
        await _activate(registry, name, budget)
        if budget > 0:
            activated_ok.add(name)
    active = {n for p in registry.get_all() if (n := _plugin_name(p))}
    active -= set(expected) - activated_ok  # never claim a plugin we did not get to
    plugins = {
        n: await _state(registry, n, active, n in expected, deadline - time.monotonic())
        for n in sorted(active | set(expected))
    }
    worker = pid if pid is not None else os.getpid()
    report = BootReport(
        pid=worker,
        workers=get_web_concurrency(),
        booted_at=now or datetime.now(UTC),
        core_version=core_version,
        plugins=plugins,
    )
    path = store.boot_dir / f"{worker}.json"
    _atomic_write(path, report.model_dump_json())
    _prune(store.boot_dir)
    return path


def _reports(store: RunStore) -> list[BootReport]:
    found: list[BootReport] = []
    for path in (
        sorted(store.boot_dir.glob("*.json")) if store.boot_dir.is_dir() else []
    ):
        try:
            found.append(
                BootReport.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            logger.warning("plugin_boot_report_skipped file=%s", path.name)
    return found


def read_boot_reports(store: RunStore, *, since: datetime) -> list[BootReport]:
    """Reports written at or after ``since`` (timezone-aware), oldest first."""
    if since.tzinfo is None:
        raise ValueError("since must be timezone-aware")
    return sorted(
        (r for r in _reports(store) if r.booted_at >= since), key=lambda r: r.booted_at
    )


def latest_active_plugins(store: RunStore) -> list[str] | None:
    """Active plugin names of the newest report; ``None`` when there is no report.

    ``None`` (the API never booted with the updater on) is not ``[]`` (it
    booted and nothing was active): only the first says nothing about what
    must survive a restart.
    """
    reports = _reports(store)
    if not reports:
        return None
    newest = max(reports, key=lambda r: r.booted_at)
    return sorted(n for n, s in newest.plugins.items() if s.active)


__all__ = [
    "BootReport",
    "PluginBootState",
    "latest_active_plugins",
    "read_boot_reports",
    "write_boot_report",
]
