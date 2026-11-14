"""Builders for the policy runner tests: a report, a fake clock, recorded events."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates._core_preflight import CoreState
from baselith.core.plugin_updates.apply.drivers._core_guards import CoreRelease
from baselith.core.plugin_updates.apply.policy_runner import PolicyRunner
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.models import (
    CheckReport,
    ReleaseInfo,
    SignedAssets,
    SystemUpdate,
    UpdateCandidate,
)
from baselith.core.plugin_updates.policy import MaintenanceWindow, UpdatePolicy
from baselith.core.plugin_updates.preflight import PreflightItem

ROME = ZoneInfo("Europe/Rome")
#: A Monday 02:00 to 04:00 window in Rome; IN is half an hour into it.
WINDOW = MaintenanceWindow(
    days=frozenset({0}), start=time(2, 0), end=time(4, 0), tz="Europe/Rome"
)
IN = datetime(2026, 11, 16, 2, 30, tzinfo=ROME)
OUT = datetime(2026, 11, 16, 5, 0, tzinfo=ROME)
BOTH = UpdatePolicy(plugins="patch", core="patch", window=WINDOW)
COMMIT = "c" * 40
LOCK = "1" * 64
CFG = UpdateApplyConfig(enabled=True, core_enabled=True)


def cand(
    plugin: str = "demo",
    installed: str | None = "1.2.3",
    latest: str = "1.2.4",
    *,
    verified: bool = True,
    sha: str | None = "a" * 64,
    available: bool = True,
    refusal: Any = None,
) -> UpdateCandidate:
    return UpdateCandidate(
        plugin=plugin,
        installed_version=installed,
        latest=ReleaseInfo(
            plugin=plugin, version=latest, tag=f"v{latest}", published_at=None
        ),
        available=available,
        refusal=refusal,
        signed_assets=SignedAssets(verified=verified, tarball_sha256=sha),
    )


def core_notice(latest: str = "1.14.1") -> SystemUpdate:
    return SystemUpdate(
        component="distribution",
        repo="owner/dist",
        installed_version="1.14.0",
        latest=ReleaseInfo(
            plugin="core", version=latest, tag=f"v{latest}", published_at=None
        ),
        available=True,
    )


def report(
    candidates: Sequence[UpdateCandidate] = (), core: SystemUpdate | None = None
) -> CheckReport:
    return CheckReport(
        checked_at=IN, candidates=list(candidates), distribution=core, system=None
    )


def core_state(
    *, migrations: str = "none", lock: str = LOCK, installed_lock: str = LOCK
) -> CoreState:
    return CoreState(
        installed_version="1.14.0",
        installed_lock_sha256=installed_lock,
        tree_dirty=False,
        release=CoreRelease(
            version="1.14.1",
            commit_sha=COMMIT,
            lock_sha256=lock,
            migrations=migrations,  # type: ignore[arg-type]
        ),
    )


class FakeCache:
    def __init__(self, found: CheckReport | None) -> None:
        self.found = found

    def load(self) -> CheckReport | None:
        return self.found


@dataclass
class Env:
    store: RunStore
    clock: list[datetime]
    events: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    preflight_calls: list[str] = field(default_factory=list)


def make_runner(
    tmp: Path,
    found: CheckReport | None,
    *,
    policy: UpdatePolicy | None = BOTH,
    now: datetime = IN,
    queue: Callable[[], int] = lambda: 0,
    preflight: Callable[[str, str], list[PreflightItem]] | None = None,
    state: CoreState | None = None,
    estimate: Callable[[str], int] = lambda subject: 300,
    store: RunStore | None = None,
) -> tuple[PolicyRunner, Env]:
    runs = store or RunStore(tmp / "apply")
    if policy is not None:
        runs.set_policy(policy, actor="test")
    env = Env(store=runs, clock=[now])

    async def emit(name: str, payload: dict[str, object]) -> bool:
        env.events.append((name, payload))
        return True

    def pre(subject: str, version: str, core: CoreState | None) -> list[PreflightItem]:
        env.preflight_calls.append(subject)
        return preflight(subject, version) if preflight else []

    async def verified(version: str) -> CoreState:
        return state or core_state()

    runner = PolicyRunner(
        store=runs,
        config=CFG,
        cache=FakeCache(found),
        queue_probe=queue,
        estimate=estimate,
        now=lambda: env.clock[0],
        emit=emit,
        preflight=pre,
        core_state=verified,
        installed_core="1.14.0",
        distribution=True,
    )
    return runner, env
