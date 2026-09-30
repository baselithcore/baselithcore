"""Rollback correctness: what must stay up, what a rollback cannot undo, crash recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.plugin_updates.apply import _activation
from core.plugin_updates.apply.boot_report import PluginBootState
from core.plugin_updates.apply.models import ApplyRun, RunKind, RunState
from core.plugin_updates.apply.swap import current_target

from ._executor_env import Env
from .fakes import write_report


def _pre_update_boot(e: Env) -> None:
    """The API as it runs before the update: demo on bundled, auth up."""
    write_report(
        e.store,
        "before",
        {
            "demo": PluginBootState(version="1.1.0", directory="x", active=True),
            "auth": PluginBootState(version="3.0.0", directory="y", active=True),
        },
        booted_at=datetime.now(UTC) - timedelta(hours=1),
    )


async def test_rollback_that_leaves_a_dependent_dead_is_rollback_failed(
    env: Env,
) -> None:
    _pre_update_boot(env)
    env.runner.boot_ok = [False, True]  # new version fails, bundled boots
    env.runner.auth_ok = [False, False]  # auth dies on the update and stays dead
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLBACK_FAILED, done.message
    assert done.must_stay_active == ["auth"]
    assert env.store.active("demo") is not None


async def test_update_that_kills_a_dependent_rolls_back(env: Env) -> None:
    _pre_update_boot(env)
    env.runner.auth_ok = [
        False,
        True,
    ]  # demo is fine but auth died; rollback revives it
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLED_BACK and done.failure == "health_failed"
    assert "auth" in done.message


class _Died(asyncio.CancelledError):
    """The updater is stopped mid-restart."""


async def test_resume_after_a_partial_boot_uses_the_persisted_list(env: Env) -> None:
    _pre_update_boot(env)
    real = env.runner.__call__

    def partial_then_dies(argv, *, env=None, timeout):  # type: ignore[no-untyped-def]
        if "schema-init" in argv:
            return real(argv, env=env, timeout=timeout)
        write_report(  # a worker came up without auth, then the updater died
            env_.store,
            "partial",
            {"demo": PluginBootState(version="1.2.0", directory="z", active=True)},
        )
        raise _Died()

    env_ = env
    env.ex._runner = partial_then_dies
    with pytest.raises(asyncio.CancelledError):
        await env.ex.execute(env.run)
    stuck = env.store.get(env.run.id)
    assert stuck is not None and stuck.must_stay_active == ["auth"]
    env.ex._runner = env.runner
    done = await env.ex.resume_activation(stuck)
    assert done.state is RunState.SUCCEEDED, done.message
    expect, _ = env.runner.at_restart[0]
    assert expect is not None and expect.must_stay_active == ["auth"]


async def test_rolled_back_detail_says_schema_changes_remain(env: Env) -> None:
    env.runner.boot_ok = [False, True]
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLED_BACK
    assert "schema changes from 1.2.0 remain" in done.message


async def test_no_schema_note_without_schema_init(env: Env) -> None:
    env.ex._config = env.ex._config.model_copy(update={"schema_init": False})
    env.runner.boot_ok = [False, True]
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLED_BACK
    assert "schema changes" not in done.message


async def test_restart_failure_then_successful_rollback_is_rolled_back(
    env: Env,
) -> None:
    env.runner.restart_rcs = [1, 0]
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLED_BACK and done.failure == "restart_failed"
    assert current_target(env.overlay, "demo") is None
    assert env.store.active("demo") is None


async def test_redo_rollback_after_a_crash_while_rolling_back(env: Env) -> None:
    env.runner.boot_ok = [False, True]
    real = env.runner.__call__
    restarts = 0

    def dies_on_second_restart(argv, *, env=None, timeout):  # type: ignore[no-untyped-def]
        nonlocal restarts
        if "schema-init" not in argv:
            restarts += 1
            if restarts == 2:
                raise _Died()
        return real(argv, env=env, timeout=timeout)

    env.ex._runner = dies_on_second_restart
    with pytest.raises(asyncio.CancelledError):
        await env.ex.execute(env.run)
    stuck = env.store.get(env.run.id)
    assert stuck is not None and stuck.state is RunState.ROLLING_BACK
    env.ex._runner = env.runner
    done = await env.ex.redo_rollback(stuck)
    assert done.state is RunState.ROLLED_BACK and done.failure == "health_failed"
    assert current_target(env.overlay, "demo") is None
    assert env.store.read_expectation("demo") is None


async def test_terminal_state_is_journaled_before_the_expectation_is_cleared(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[RunState] = []
    real = env.store.clear_expectation

    def spy(plugin: str, run_id: str | None = None) -> None:
        run = env.store.get(env.run.id)
        assert run is not None
        seen.append(run.state)
        real(plugin, run_id)

    monkeypatch.setattr(env.store, "clear_expectation", spy)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.SUCCEEDED
    assert seen == [RunState.SUCCEEDED]


def _rollback_run(e: Env, target: str | None) -> ApplyRun:
    return e.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="x",
        to_version="y",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target=target,
    )


async def _installed_then_back_on_bundled(e: Env) -> None:
    assert (await e.ex.execute(e.run)).state is RunState.SUCCEEDED
    assert (await e.ex.execute(_rollback_run(e, None))).state is RunState.SUCCEEDED


async def test_an_unverified_target_is_never_linked(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _installed_then_back_on_bundled(env)
    (env.overlay / ".store" / "demo-1.2.0" / "__init__.py").write_text("X = 666\n")
    linked: list[str | None] = []
    real = _activation.point_to

    def spy(overlay, plugin, target, *, run_id):  # type: ignore[no-untyped-def]
        linked.append(target)
        real(overlay, plugin, target, run_id=run_id)

    monkeypatch.setattr(_activation, "point_to", spy)
    done = await env.ex.execute(_rollback_run(env, "demo-1.2.0"))
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert "demo-1.2.0" not in linked
    for folder in (env.overlay, env.overlay / ".store"):
        assert not [p for p in folder.iterdir() if p.name.startswith(".staging-")]


async def test_target_overtaken_by_the_bundled_plugin_falls_back_to_bundled(
    env: Env,
) -> None:
    await _installed_then_back_on_bundled(env)
    (env.bundled / "demo" / "manifest.yaml").write_text("name: demo\nversion: 1.3.0\n")
    done = await env.ex.execute(_rollback_run(env, "demo-1.2.0"))
    assert done.state is RunState.SUCCEEDED, done.message
    assert done.target is None and current_target(env.overlay, "demo") is None


async def test_unreadable_bundled_version_is_not_a_fallback(env: Env) -> None:
    await _installed_then_back_on_bundled(env)
    (env.bundled / "demo" / "manifest.yaml").write_text("name: demo\nversion: banana\n")
    restarts = env.runner.restarts
    done = await env.ex.execute(_rollback_run(env, "demo-1.2.0"))
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert env.runner.restarts == restarts


async def test_missing_schema_env_file_refuses_before_the_swap(env: Env) -> None:
    assert env.ex._config.schema_env_file is not None
    env.ex._config.schema_env_file.unlink()
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "migration_failed"
    assert str(env.tmp) not in done.message and "owner.env" not in done.message
    assert current_target(env.overlay, "demo") is None and env.runner.calls == []


async def test_failed_download_leaves_no_temp_file(env: Env) -> None:
    env.cache.tarball_path("demo", "1.2.0").unlink()
    env.fetcher.tarball_error = OSError("disk full")
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert not list(env.cache.tarball_path("demo", "1.2.0").parent.glob(".download-*"))
