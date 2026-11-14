"""The live inputs of a pre-flight: the store, the task queue and the disk.

:func:`~.preflight.build_preflight` is pure; this module reads what it needs
on this host and hands it over. Every read is guarded: a store, queue or disk
that cannot answer becomes a missing input or a ``warn`` item, never an error.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ._core_preflight import CoreState
from ._core_state import local_core_state
from .apply.boot_report import latest_active_plugins
from .apply.models import CORE_CLAIM, CORE_SUBJECT, PLUGIN_SUBJECT_PREFIX
from .apply.store import RunStore
from .models import CheckReport
from .preflight import PreflightItem, build_preflight, normalize_subject
from .upgrade import installed_bounds
from .upgrade_models import InstallMethod

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _guarded(what: str, read: Callable[[], T]) -> T | None:
    try:
        return read()
    except Exception:  # an input that cannot be read is an unknown, not an error
        logger.debug("preflight %s unavailable", what, exc_info=True)
        return None


def queue_jobs() -> int:
    """Jobs waiting or running across the configured task queues.

    Raises:
        RuntimeError: A queue could not be read (Redis down); the monitor
            itself swallows that and answers None, which must not read as 0.
    """
    from baselith.core.config import get_task_queue_config
    from baselith.core.task_queue.monitor import get_worker_monitor

    monitor = get_worker_monitor()
    total = 0
    for name in get_task_queue_config().queues:
        info = monitor.get_queue_info(name)
        if info is None:
            raise RuntimeError(f"queue {name} unavailable")
        total += info.job_count + info.started_job_count
    return total


def disk_free(path: Path) -> int:
    """Free bytes on the filesystem holding ``path`` (or its nearest parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


def live_preflight(
    subject: str,
    version: str | None,
    *,
    report: CheckReport | None,
    config: UpdateApplyConfig,
    method: InstallMethod,
    bundled_root: Path,
    core_version: str,
    distribution: bool,
    store: RunStore | None = None,
    queue_probe: Callable[[], int] | None = None,
    now: datetime | None = None,
    core: CoreState | None = None,
) -> list[PreflightItem]:
    """The pre-flight of ``subject`` with this host's live inputs.

    ``queue_probe`` defaults to :func:`queue_jobs` (the task queue in Redis).
    For the core subject ``core`` defaults to :func:`~._core_state.local_core_state`
    (the checkout only; the release is verified when an install is requested).
    """
    runs = store if store is not None else RunStore(config.state_dir)
    key = normalize_subject(subject)
    claim = (
        CORE_CLAIM if key == CORE_SUBJECT else key.removeprefix(PLUGIN_SUBJECT_PREFIX)
    )
    if key == CORE_SUBJECT and core is None:
        core = _guarded("checkout state", lambda: local_core_state(config))
    return build_preflight(
        key,
        version,
        report=report,
        config=config,
        heartbeat=_guarded("heartbeat", runs.read_heartbeat),
        active_run=_guarded("active run", lambda: runs.active(claim)),
        bounds=_guarded("plugin bounds", lambda: installed_bounds(bundled_root)),
        queue_probe=queue_probe or queue_jobs,
        disk_free=disk_free,
        now=now or datetime.now(UTC),
        method=method,
        core_version=core_version,
        distribution=distribution,
        boot_probe=lambda: latest_active_plugins(runs),
        core=core,
    )


__all__ = ["disk_free", "live_preflight", "queue_jobs"]
