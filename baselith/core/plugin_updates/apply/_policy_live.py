"""The policy runner with this host's live inputs (the updater's own wiring).

Kept apart from :mod:`.policy_runner`, which is pure apart from its seams: the
cache of the last report, the task-queue probe, the live pre-flight, the
verified distribution manifest and the event bus are read here.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from .._core_preflight import CoreState
from ..preflight import PreflightItem
from .policy_runner import PolicyRunner, journal_estimate
from .store import RunStore


def build_policy_runner(config: UpdateApplyConfig, store: RunStore) -> PolicyRunner:
    """The runner the updater ticks, reading the same store it executes from."""
    from baselith.core import _version
    from baselith.core._core_version import CORE_VERSION
    from baselith.core.config.plugin_updates import get_plugin_update_config
    from baselith.core.config.plugins import get_plugin_config

    from .._core_state import default_fetcher, verified_core_state
    from .._preflight_live import live_preflight, queue_jobs
    from ..cache import UpdateCache
    from ..distribution import distribution_name
    from ..upgrade import detect_install_method
    from ._events import emit_update_event

    update_cfg = get_plugin_update_config()
    cache = UpdateCache(update_cfg.cache_dir)
    method, _ = detect_install_method(update_cfg)
    distribution = distribution_name() is not None
    bundled = Path(get_plugin_config().plugins_path)
    fetcher = default_fetcher(update_cfg)

    def preflight(
        subject: str, version: str, core: CoreState | None
    ) -> Sequence[PreflightItem]:
        return live_preflight(
            subject,
            version,
            report=cache.load(),
            config=config,
            method=method,
            bundled_root=bundled,
            core_version=CORE_VERSION,
            distribution=distribution,
            store=store,
            core=core,
        )

    async def core_state(version: str) -> CoreState:
        return await verified_core_state(version, config=config, fetcher=fetcher)

    def estimate(subject: str) -> int:
        return journal_estimate(store, subject, fallback=config.policy_estimate_seconds)

    return PolicyRunner(
        store=store,
        config=config,
        cache=cache,
        queue_probe=queue_jobs,
        estimate=estimate,
        now=lambda: datetime.now(UTC),
        emit=emit_update_event,
        preflight=preflight,
        core_state=core_state,
        installed_core=str(_version.__version__),
        distribution=distribution,
    )


__all__ = ["build_policy_runner"]
