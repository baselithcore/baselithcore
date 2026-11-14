"""CoreHostDriver: a signed core release installed in place, and every way back."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from baselith.core.plugin_updates.apply.models import (
    CORE_CLAIM,
    RunKind,
    RunState,
)
from baselith.core.plugin_updates.apply.store import RunConflict

from ._core_env import (
    NEW,
    OLD,
    build_core_env,
    create_run,
    sha,
    signed_manifest,
    states,
)


@pytest.fixture(autouse=True)
def _host_install(monkeypatch: pytest.MonkeyPatch) -> None:
    """The machine running the tests may be a container: pretend a host."""
    monkeypatch.setattr(
        "baselith.core.plugin_updates.apply.drivers._core_guards.host_install_ok",
        lambda method, **_: method in ("source", "pip", "custom"),
    )


async def test_happy_path_ends_finalizing_in_order(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    done = await env.driver.execute(env.run)
    assert done.state is RunState.FINALIZING, done.message
    assert env.log == [
        "backup",
        "git fetch",
        f"git checkout {NEW[:4]}",
        "db migrate",
        "schema-init",
        "restart",
    ]
    assert states(env, done.id) == [
        "approved",
        "preparing",
        "migrating",
        "activating",
        "health_checking",
        "finalizing",
    ]
    assert done.previous_target == OLD and done.target == NEW
    assert done.must_stay_active == ["auth"]
    assert env.git.head == NEW
    assert env.store.active(CORE_CLAIM) is not None  # finalizing keeps the claim


async def test_manifest_commit_must_equal_the_pinned_commit(tmp_path: Path) -> None:
    env = build_core_env(tmp_path, pin="3" * 40)
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "commit_mismatch")
    assert env.log == [] and env.git.writes == []


async def test_unsigned_manifest_changes_nothing(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.fetcher.raw = signed_manifest(env.priv).replace(b'"manifest_signature', b'"x')
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "signature_invalid")
    assert env.log == [] and env.git.writes == []


async def test_manifest_signed_for_a_plugin_name_is_refused(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.fetcher.raw = signed_manifest(env.priv, name="auth")
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "name_mismatch")


async def test_unreachable_release_is_verification_failed(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.fetcher.error = OSError("network down")
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "verification_failed")
    assert env.git.writes == []


async def test_backup_failure_changes_nothing(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.runner.backup_rc = 3
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "backup_failed")
    assert env.log == ["backup"] and env.git.writes == []


async def test_missing_backup_command_never_reaches_git(tmp_path: Path) -> None:
    env = build_core_env(tmp_path, backup_command=[])
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "backup_unconfigured")
    assert env.log == [] and env.git.writes == []


async def test_dirty_tree_changes_nothing(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.git.status = " M baselith/core/x.py\n"
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "tree_dirty")
    assert env.log == [] and env.git.writes == []


async def test_ignored_paths_do_not_make_the_tree_dirty(tmp_path: Path) -> None:
    env = build_core_env(tmp_path, ignored_paths=["ui/dist"])
    env.git.status = " M ui/dist/app.js\n"
    assert (await env.driver.execute(env.run)).state is RunState.FINALIZING


async def test_changed_lock_is_a_blocker_not_an_install(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.fetcher.raw = signed_manifest(env.priv, lock_sha256=sha(b"other\n"))
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "needs_environment_update")
    assert env.log == [] and env.git.writes == []


async def test_breaking_migration_is_never_installed(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.fetcher.raw = signed_manifest(env.priv, migrations="breaking")
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "breaking_migration")
    assert env.log == []


async def test_lock_changed_after_checkout_restores_previous_head(
    tmp_path: Path,
) -> None:
    env = build_core_env(tmp_path)
    env.git.locks[NEW] = b"a lock the manifest does not name\n"
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "lock_mismatch")
    assert env.git.head == OLD
    assert "db migrate" not in env.log and env.runner.restarts == 0


async def test_failed_checkout_leaves_head_and_restarts_nothing(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.git.checkout_fails.add(NEW)
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "checkout_failed")
    assert env.git.head == OLD and env.runner.restarts == 0


async def test_unfetchable_commit_aborts_before_any_file_changes(
    tmp_path: Path,
) -> None:
    env = build_core_env(tmp_path)
    env.git.remote.discard(NEW)
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "checkout_failed")
    assert not any(c[0] == "checkout" for c in env.git.calls)


async def test_migration_failure_restores_head_and_does_not_restart(
    tmp_path: Path,
) -> None:
    env = build_core_env(tmp_path)
    env.runner.migrate_rc = 1
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "migration_failed")
    assert "the schema may have advanced" in done.message
    assert env.git.head == OLD and env.runner.restarts == 0


async def test_health_failure_rolls_back_and_restarts_twice(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.runner.boot_ok = [False]
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.ROLLED_BACK, "health_failed")
    assert env.runner.restarts == 2 and env.git.head == OLD
    assert "schema changes from 1.15.0 remain" in done.message
    assert env.store.active(CORE_CLAIM) is None  # rolled back releases the claim


async def test_rollback_failure_keeps_the_claim(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.runner.boot_ok = [False]
    env.runner.restart_rcs = [0, 1]
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.ROLLBACK_FAILED, "health_failed")
    holder = env.store.active(CORE_CLAIM)
    assert holder is not None and holder.id == done.id


async def test_two_core_runs_conflict(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    with pytest.raises(RunConflict):
        create_run(env.store)


async def test_core_and_plugin_runs_do_not_conflict(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    plugin_run = env.store.create(
        kind=RunKind.UPDATE,
        plugin="aura",
        from_version="1.0.0",
        to_version="1.1.0",
        tarball_sha256="a" * 64,
        requested_by="cli:op",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    assert plugin_run.id != env.run.id
    assert env.store.active("aura") is not None
    assert env.store.active(CORE_CLAIM) is not None


async def test_secrets_are_not_in_any_logged_argv(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    env = build_core_env(
        tmp_path, backup_command=["/usr/local/bin/backup", "--password=s3cr3t-arg"]
    )
    env.runner.migrate_rc = 2
    done = await env.driver.execute(env.run)
    assert done.failure == "migration_failed"
    migrate_env = next(e for a, e in env.runner.calls if a[-2:] == ["db", "migrate"])
    assert (
        migrate_env is not None
        and migrate_env["POSTGRES_PASSWORD"] == "pw-owner-secret"
    )
    served = done.model_dump_json() + "".join(
        t.model_dump_json() for t in env.store.journal(done.id)
    )
    for secret in ("s3cr3t-arg", "pw-owner-secret"):
        assert secret not in caplog.text and secret not in served


async def test_disabled_core_changes_nothing(tmp_path: Path) -> None:
    env = build_core_env(tmp_path, core_enabled=False)
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "apply_disabled")
    assert env.fetcher.calls == [] and env.log == []


async def test_no_updater_restart_command_changes_nothing(tmp_path: Path) -> None:
    env = build_core_env(tmp_path, updater_restart_command=[])
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (
        RunState.FAILED,
        "updater_restart_unconfigured",
    )
    assert env.log == []  # no backup, no checkout, no restart


async def test_no_boot_report_refuses(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    (env.store.boot_dir / "pre.json").unlink()
    done = await env.driver.execute(env.run)
    assert (done.state, done.failure) == (RunState.FAILED, "apply_disabled")
    assert "no boot report" in done.message


async def test_resume_activation_after_a_crash_between_restart_and_health(
    tmp_path: Path,
) -> None:
    env = build_core_env(tmp_path)
    run = env.store.transition(
        env.run.id,
        RunState.MIGRATING,
        previous_target=OLD,
        target=NEW,
        must_stay_active=["auth"],
    )
    env.git.local.add(NEW)
    env.git.head = NEW
    run = env.store.transition(run.id, RunState.HEALTH_CHECKING)
    done = await env.driver.resume_activation(run)
    assert done.state is RunState.FINALIZING and env.runner.restarts == 1


async def test_redo_rollback_puts_the_previous_commit_back(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    run = env.store.transition(
        env.run.id,
        RunState.MIGRATING,
        previous_target=OLD,
        target=NEW,
        must_stay_active=["auth"],
    )
    env.git.local.add(NEW)
    env.git.head = NEW
    run = env.store.transition(
        run.id, RunState.ROLLING_BACK, failure="health_failed", message="boom"
    )
    done = await env.driver.redo_rollback(run)
    assert done.state is RunState.ROLLED_BACK and env.git.head == OLD


async def test_undo_swap_in_migrating_checks_out_previous_head(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    run = env.store.transition(
        env.run.id, RunState.MIGRATING, previous_target=OLD, target=NEW
    )
    env.git.local.add(NEW)
    env.git.head = NEW
    env.driver.undo_swap(run)
    assert env.git.head == OLD
    env.git.checkout_fails.add(OLD)
    env.git.head = NEW
    with pytest.raises(ValueError):
        env.driver.undo_swap(run)


async def test_rollback_run_skips_manifest_and_migrations(tmp_path: Path) -> None:
    env = build_core_env(tmp_path)
    env.store.transition(env.run.id, RunState.FAILED, failure="x")
    env.git.local.add(NEW)
    env.git.head = NEW
    (env.git.root / "uv.lock").write_bytes(env.git.locks[NEW])
    back = create_run(env.store, kind=RunKind.ROLLBACK, pin=OLD)
    done = await env.driver.execute(back)
    assert done.state is RunState.FINALIZING, done.message
    assert env.fetcher.calls == []
    assert "db migrate" not in env.log and env.git.head == OLD
