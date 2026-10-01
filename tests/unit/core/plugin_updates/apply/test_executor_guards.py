"""Executor guards: foreign expectations, verify I/O errors, early refusals."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime

import pytest

from core.plugin_updates.apply import executor as executor_mod
from core.plugin_updates.apply.models import ApplyRun, Expectation, RunKind, RunState
from core.plugin_updates.apply.swap import current_target

from ._executor_env import Env, served_text

OTHER = "pinstall-20260930T120000Z-0000abcd"


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


async def test_a_later_runs_expectation_is_never_cleared(env: Env) -> None:
    foreign = Expectation(
        run_id=OTHER,
        plugin="demo",
        version="9.9.9",
        store_dir=None,
        restart_at=datetime.now(UTC),
        must_stay_active=[],
    )
    env.store.write_expectation(foreign)
    env.ex._config = env.ex._config.model_copy(update={"enabled": False})
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED
    assert env.store.read_expectation("demo") == foreign


async def test_verify_io_error_is_verification_failed_and_releases(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await env.ex.execute(env.run)).state is RunState.SUCCEEDED
    assert (await env.ex.execute(_rollback_run(env, None))).state is RunState.SUCCEEDED

    def boom(**_: object) -> str:
        raise OSError(f"cannot create {env.tmp}/ov/.store/x")

    monkeypatch.setattr(executor_mod.tempfile, "mkdtemp", boom)
    run = _rollback_run(env, "demo-1.2.0")
    done = await env.ex.execute(run)
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert str(env.tmp) not in served_text(env, done)
    assert env.store.active("demo") is None
    assert current_target(env.overlay, "demo") is None


async def test_missing_schema_env_refuses_before_staging(env: Env) -> None:
    assert env.ex._config.schema_env_file is not None
    env.ex._config.schema_env_file.unlink()
    done = await env.ex.execute(env.run)
    assert done.failure == "migration_failed"
    assert env.fetcher.calls == []
    assert not (env.overlay / ".store").exists()


@pytest.mark.parametrize("rollback", [False, True])
async def test_no_boot_report_refuses_before_anything(
    env: Env, caplog: pytest.LogCaptureFixture, rollback: bool
) -> None:
    shutil.rmtree(env.store.boot_dir)
    run = env.run
    if rollback:
        env.ex._config = env.ex._config.model_copy(update={"enabled": False})
        await env.ex.execute(env.run)  # release the claim
        env.ex._config = env.ex._config.model_copy(update={"enabled": True})
        run = _rollback_run(env, None)
    with caplog.at_level(logging.WARNING):
        done = await env.ex.execute(run)
    assert done.state is RunState.FAILED and done.failure == "apply_disabled"
    assert done.message == (
        "no boot report yet — restart the API once with UPDATE_APPLY_ENABLED on"
    )
    assert env.fetcher.calls == [] and env.runner.calls == []
    assert current_target(env.overlay, "demo") is None
    assert "plugin_update_no_boot_report" in caplog.text


async def test_resume_of_an_old_run_without_a_boot_report_never_succeeds(
    env: Env,
) -> None:
    assert (await env.ex.execute(env.run)).state is RunState.SUCCEEDED
    run = _rollback_run(env, None)
    for state in (RunState.PREPARING, RunState.MIGRATING, RunState.ACTIVATING):
        run = env.store.transition(run.id, state, previous_target="demo-1.2.0")
    assert run.must_stay_active is None  # recorded before the field existed
    env.ex._swap_locked("demo", None, run.id)
    shutil.rmtree(env.store.boot_dir)
    restarts = env.runner.restarts
    done = await env.ex.resume_activation(run)
    assert done.state is not RunState.SUCCEEDED
    assert "no boot report" in done.message
    assert env.runner.restarts > restarts  # rolled back to where it was
    assert current_target(env.overlay, "demo") == "demo-1.2.0"


async def test_a_release_needing_a_host_build_is_refused_before_any_change(
    env: Env,
) -> None:
    from ..test_checker import _signed_tarball

    tgz, meta = _signed_tarball(
        env.tmp / "hb", env.priv, extra_manifest="host_build_required: true\n"
    )
    env.cache.tarball_path("demo", "1.2.0").write_bytes(tgz.read_bytes())
    env.fetcher.meta = meta
    env.store.transition(env.run.id, RunState.FAILED)  # free the claim
    run = env.store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256=meta["tarball_sha256"],
        requested_by="alice",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    done = await env.ex.execute(run)
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert "host_build_required" in done.message
    assert env.runner.calls == [] and current_target(env.overlay, "demo") is None
    assert not (env.overlay / ".store" / "demo-1.2.0").exists()
