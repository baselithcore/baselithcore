"""The policy runner: pre-approved patch runs inside the window, failing closed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from baselith.core.plugin_updates.apply import CORE_CLAIM, RunConflict, RunState
from baselith.core.plugin_updates.apply.models import RunKind
from baselith.core.plugin_updates.apply.policy_runner import (
    POLICY_ACTOR,
    journal_estimate,
)
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.models import Refusal
from baselith.core.plugin_updates.policy import POLICY_OFF, UpdatePolicy
from baselith.core.plugin_updates.preflight import PreflightItem, PreflightLevel

from ._policy_env import (
    COMMIT,
    IN,
    OUT,
    WINDOW,
    cand,
    core_notice,
    core_state,
    make_runner,
    report,
)

APPLIED = "update.policy_applied"
DEFERRED = "update.policy_deferred"


async def test_policy_off_by_default(tmp_path: Path) -> None:
    runner, env = make_runner(tmp_path, report([cand()]), policy=None)
    assert env.store.get_policy() == POLICY_OFF
    assert await runner.tick() == []
    assert env.store.runs() == [] and env.events == []


async def test_outside_window_creates_nothing(tmp_path: Path) -> None:
    runner, env = make_runner(tmp_path, report([cand()]), now=OUT)
    assert await runner.tick() == []
    assert env.store.runs() == [] and env.events == []


async def test_patch_in_window_creates_a_preapproved_run_with_policy_actor(
    tmp_path: Path,
) -> None:
    runner, env = make_runner(tmp_path, report([cand()]))
    (run_id,) = await runner.tick()
    run = env.store.get(run_id)
    assert run is not None
    assert run.state is RunState.APPROVED and run.kind is RunKind.UPDATE
    assert run.requested_by == POLICY_ACTOR == "policy:auto-patch"
    assert (run.plugin, run.from_version, run.to_version) == ("demo", "1.2.3", "1.2.4")
    assert run.tarball_sha256 == "a" * 64
    assert env.events == [
        (
            APPLIED,
            {
                "subject": "plugin:demo",
                "from_version": "1.2.3",
                "to_version": "1.2.4",
                "run_id": run_id,
            },
        )
    ]


async def test_minor_and_major_are_never_applied(tmp_path: Path) -> None:
    found = report([cand("a", latest="1.3.0"), cand("b", latest="2.0.0")])
    runner, env = make_runner(tmp_path, found)
    assert await runner.tick() == []
    assert env.store.runs() == []


@pytest.mark.parametrize(
    ("installed", "latest"),
    [("1.2.3", "1.2.4-rc1"), ("1.2.3-rc1", "1.2.3"), ("1.2", "1.2.1"), (None, "1.2.4")],
)
async def test_prerelease_and_unparsable_fail_closed(
    tmp_path: Path, installed: str | None, latest: str
) -> None:
    runner, env = make_runner(
        tmp_path, report([cand(installed=installed, latest=latest)])
    )
    assert await runner.tick() == []
    assert env.store.runs() == []


async def test_unsigned_or_refused_releases_are_skipped(tmp_path: Path) -> None:
    found = report(
        [
            cand("a", verified=False),
            cand("b", sha=None),
            cand("c", refusal=Refusal.SIGNATURE_INVALID),
            cand("d", available=False),
        ]
    )
    runner, env = make_runner(tmp_path, found)
    assert await runner.tick() == []


async def test_plugins_before_core_one_per_tick(tmp_path: Path) -> None:
    found = report([cand("zeta"), cand("alpha")], core=core_notice())
    runner, env = make_runner(tmp_path, found)
    first = await runner.tick()
    second = await runner.tick()
    third = await runner.tick()
    plugins = [env.store.get(i).plugin for i in first + second + third]  # type: ignore[union-attr]
    assert plugins == ["alpha", "zeta"]  # core waits while plugin runs are unfinished
    for run_id in first + second:
        env.store.transition(run_id, RunState.SUCCEEDED)
    (core_id,) = await runner.tick()
    core = env.store.get(core_id)
    assert core is not None
    assert (core.plugin, core.subject, core.tarball_sha256) == (
        CORE_CLAIM,
        "core",
        COMMIT,
    )
    assert (core.from_version, core.to_version) == ("1.14.0", "1.14.1")
    assert core.requested_by == POLICY_ACTOR
    assert env.events[-1][1]["subject"] == "core"


async def test_core_only_when_its_channel_is_on(tmp_path: Path) -> None:
    policy = UpdatePolicy(plugins="patch", window=WINDOW)
    runner, env = make_runner(tmp_path, report(core=core_notice()), policy=policy)
    assert await runner.tick() == []


async def test_busy_queue_defers_with_an_event(tmp_path: Path) -> None:
    runner, env = make_runner(tmp_path, report([cand()]), queue=lambda: 3)
    assert await runner.tick() == []
    assert env.store.runs() == []
    assert env.events == [(DEFERRED, {"code": "queue_busy", "subject": "plugin:demo"})]


async def test_probe_error_defers_never_applies(tmp_path: Path) -> None:
    def down() -> int:
        raise RuntimeError("redis down")

    runner, env = make_runner(tmp_path, report([cand()]), queue=down)
    assert await runner.tick() == []
    assert env.store.runs() == []
    assert env.events == [
        (DEFERRED, {"code": "probe_failed", "subject": "plugin:demo"})
    ]


async def test_nothing_to_apply_never_probes_or_emits(tmp_path: Path) -> None:
    def boom() -> int:  # pragma: no cover - must not be called
        raise AssertionError

    runner, env = make_runner(tmp_path, report([cand(latest="1.2.3")]), queue=boom)
    assert await runner.tick() == [] and env.events == []


async def test_estimate_longer_than_remaining_defers(tmp_path: Path) -> None:
    late = IN.replace(hour=3, minute=56)  # four minutes left
    runner, env = make_runner(tmp_path, report([cand()]), now=late)
    assert await runner.tick() == []
    assert env.events == [
        (DEFERRED, {"code": "window_too_short", "subject": "plugin:demo"})
    ]
    runner, env = make_runner(
        tmp_path / "b",
        report([cand()]),
        now=late,
        estimate=lambda s: 240,  # exactly what is left: it fits
    )
    assert len(await runner.tick()) == 1


async def test_allowlist_restricts_plugins(tmp_path: Path) -> None:
    policy = UpdatePolicy(plugins="patch", window=WINDOW, plugin_allowlist={"beta"})
    found = report([cand("alpha"), cand("beta")])
    runner, env = make_runner(tmp_path, found, policy=policy)
    (run_id,) = await runner.tick()
    assert env.store.get(run_id).plugin == "beta"  # type: ignore[union-attr]
    assert await runner.tick() == []


async def test_preflight_block_skips_the_subject(tmp_path: Path) -> None:
    def blocked(subject: str, version: str) -> list[PreflightItem]:
        if subject == "plugin:alpha":
            return [PreflightItem(code="x", level=PreflightLevel.BLOCK, message="no")]
        return [PreflightItem(code="y", level=PreflightLevel.WARN, message="hm")]

    runner, env = make_runner(
        tmp_path, report([cand("alpha"), cand("beta")]), preflight=blocked
    )
    (run_id,) = await runner.tick()
    assert env.store.get(run_id).plugin == "beta"  # type: ignore[union-attr]


async def test_preflight_error_skips_the_subject(tmp_path: Path) -> None:
    def broken(subject: str, version: str) -> list[PreflightItem]:
        raise OSError("disk")

    runner, env = make_runner(tmp_path, report([cand()]), preflight=broken)
    assert await runner.tick() == []


@pytest.mark.parametrize("migrations", ["expand", "breaking"])
async def test_core_requires_migrations_none(tmp_path: Path, migrations: str) -> None:
    runner, env = make_runner(
        tmp_path, report(core=core_notice()), state=core_state(migrations=migrations)
    )
    assert await runner.tick() == []


async def test_core_with_changed_lock_is_not_applied(tmp_path: Path) -> None:
    state = core_state(lock="2" * 64)
    runner, env = make_runner(tmp_path, report(core=core_notice()), state=state)
    assert await runner.tick() == []


async def test_core_minor_is_not_applied(tmp_path: Path) -> None:
    runner, env = make_runner(tmp_path, report(core=core_notice("1.15.0")))
    assert await runner.tick() == []


async def test_core_unverified_release_is_not_applied(tmp_path: Path) -> None:
    state = core_state().model_copy(update={"release": None, "release_detail": "x"})
    runner, env = make_runner(tmp_path, report(core=core_notice()), state=state)
    assert await runner.tick() == []


async def test_active_run_blocks_the_subject(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "apply")
    store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.2.3",
        to_version="1.2.4",
        tarball_sha256="a" * 64,
        requested_by="alice",
        approval_required=True,
        approval_ttl_seconds=600,
    )
    runner, env = make_runner(tmp_path, report([cand()]), store=store)
    assert await runner.tick() == []
    assert len(store.runs()) == 1


async def test_run_conflict_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner, env = make_runner(tmp_path, report([cand()]))

    def lost(**_: object) -> None:
        raise RunConflict("pinstall-20261116T013000Z-00000000")

    monkeypatch.setattr(env.store, "create", lost)
    assert await runner.tick() == []
    assert not any(name == APPLIED for name, _ in env.events)


async def test_second_tick_in_the_window_does_nothing(tmp_path: Path) -> None:
    runner, env = make_runner(tmp_path, report([cand()]))
    (run_id,) = await runner.tick()
    env.store.transition(run_id, RunState.SUCCEEDED)
    # the cache still says 1.2.4 is available until the next check: the
    # succeeded run already reached it, so nothing is created again
    assert await runner.tick() == []


async def test_no_report_creates_nothing(tmp_path: Path) -> None:
    runner, env = make_runner(tmp_path, None)
    assert await runner.tick() == []


def test_journal_estimate_is_the_median_of_recent_successes(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "apply")
    base = datetime(2026, 11, 1, tzinfo=UTC)
    for i, minutes in enumerate([1, 2, 30, 3, 4, 5]):
        at = base + timedelta(days=i)
        run = store.create(
            kind=RunKind.UPDATE,
            plugin="demo",
            from_version="1",
            to_version="2",
            tarball_sha256="a" * 64,
            requested_by="a",
            approval_required=False,
            approval_ttl_seconds=60,
            now=at,
        )
        store.transition(run.id, RunState.PREPARING, now=at)
        store.transition(
            run.id, RunState.SUCCEEDED, now=at + timedelta(minutes=minutes)
        )
    # the last five: 2, 30, 3, 4, 5 minutes -> median 4
    assert journal_estimate(store, "plugin:demo", fallback=999) == 240
    assert journal_estimate(store, "plugin:other", fallback=999) == 999
    assert journal_estimate(store, "core", fallback=77) == 77


@pytest.mark.parametrize(
    "outcome", [RunState.ROLLED_BACK, RunState.FAILED, RunState.SUCCEEDED]
)
async def test_a_release_already_tried_is_never_retried(
    tmp_path: Path, outcome: RunState
) -> None:
    runner, env = make_runner(tmp_path, report([cand()]))
    (run_id,) = await runner.tick()
    env.store.transition(run_id, outcome)
    assert await runner.tick() == []
    assert len(env.store.runs()) == 1


async def test_a_denied_manual_request_does_not_block_the_policy(
    tmp_path: Path,
) -> None:
    store = RunStore(tmp_path / "apply")
    manual = store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.2.3",
        to_version="1.2.4",
        tarball_sha256="a" * 64,
        requested_by="alice",
        approval_required=True,
        approval_ttl_seconds=600,
    )
    store.transition(manual.id, RunState.DENIED)
    runner, env = make_runner(tmp_path, report([cand()]), store=store)
    assert len(await runner.tick()) == 1


async def test_host_build_releases_are_skipped(tmp_path: Path) -> None:
    c = cand()
    assert c.signed_assets is not None
    c = c.model_copy(
        update={
            "signed_assets": c.signed_assets.model_copy(
                update={"host_build_required": True}
            )
        }
    )
    runner, env = make_runner(tmp_path, report([c]))
    assert await runner.tick() == []


async def test_live_runner_builds_and_is_off_without_a_policy(tmp_path: Path) -> None:
    from baselith.core.config.plugin_update_apply import UpdateApplyConfig
    from baselith.core.plugin_updates.apply._policy_live import build_policy_runner
    from baselith.core.plugin_updates.apply.policy_runner import PolicyRunner

    store = RunStore(tmp_path / "apply")
    runner = build_policy_runner(UpdateApplyConfig(state_dir=store.root), store)
    assert isinstance(runner, PolicyRunner)
    assert await runner.tick() == []
