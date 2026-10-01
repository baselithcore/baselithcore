"""The executor's state machine: install, migrate, restart, verify, roll back."""

from __future__ import annotations

import asyncio
import logging
import sys

import pytest

from core.plugin_updates.apply.executor import SCHEMA_INIT_ARGV
from core.plugin_updates.apply.models import RunKind, RunState
from core.plugin_updates.apply.swap import current_target

from ._executor_env import Env


def test_schema_init_argv() -> None:
    assert SCHEMA_INIT_ARGV("demo") == [
        sys.executable,
        "-m",
        "core.cli",
        "plugin",
        "schema-init",
        "--plugin",
        "demo",
    ]


async def test_update_succeeds(env: Env) -> None:
    done = await env.ex.execute(env.run)
    assert done.state is RunState.SUCCEEDED, done.message
    assert current_target(env.overlay, "demo") == "demo-1.2.0"
    assert (env.overlay / ".store" / "demo-1.2.0" / ".env").read_text() == "K=v\n"
    assert env.store.active("demo") is None
    assert env.store.read_expectation("demo") is None
    assert done.previous_target is None and done.target == "demo-1.2.0"
    assert [t.state for t in env.store.journal(env.run.id)] == [
        RunState.APPROVED,
        RunState.PREPARING,
        RunState.MIGRATING,
        RunState.ACTIVATING,
        RunState.HEALTH_CHECKING,
        RunState.SUCCEEDED,
    ]


async def test_schema_init_runs_with_owner_env_before_restart(env: Env) -> None:
    await env.ex.execute(env.run)
    (schema_argv, schema_env), (restart_argv, restart_env) = env.runner.calls[:2]
    assert schema_argv == SCHEMA_INIT_ARGV("demo")
    assert schema_env is not None and schema_env["POSTGRES_USER"] == "owner"
    assert restart_argv == ["systemctl", "restart", "api"]
    assert restart_env is None  # the owner credential never reaches the restart


async def test_migration_failure_leaves_the_link_and_does_not_restart(env: Env) -> None:
    env.runner.schema_rc = 3
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "migration_failed"
    assert current_target(env.overlay, "demo") is None  # never left bundled
    assert env.runner.restarts == 0 and env.store.active("demo") is None
    assert env.store.read_expectation("demo") is None


async def test_release_json_changed_since_request(env: Env) -> None:
    env.fetcher.meta = env.meta | {"tarball_sha256": "0" * 64}
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED
    assert done.failure in {"release_changed", "verification_failed"}
    assert current_target(env.overlay, "demo") is None and env.runner.calls == []


async def test_health_failure_rolls_back(env: Env) -> None:
    env.runner.boot_ok = [False, True]  # the new version fails, the previous one boots
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLED_BACK and done.failure == "health_failed"
    assert current_target(env.overlay, "demo") is None and env.runner.restarts == 2
    assert env.store.active("demo") is None
    assert env.store.read_expectation("demo") is None
    assert [t.state for t in env.store.journal(env.run.id)][-2:] == [
        RunState.ROLLING_BACK,
        RunState.ROLLED_BACK,
    ]


async def test_failed_rollback_keeps_claim(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    env.runner.boot_ok = [False, False]
    with caplog.at_level(logging.WARNING):
        done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLBACK_FAILED and done.failure == "health_failed"
    assert env.store.active("demo") is not None
    assert env.store.read_expectation("demo") is None
    assert any(r.levelname == "CRITICAL" for r in caplog.records)


async def test_restart_command_failure_rolls_back(env: Env) -> None:
    env.runner.restart_rc = 1
    done = await env.ex.execute(env.run)
    assert done.failure == "restart_failed"
    assert done.state in {RunState.ROLLED_BACK, RunState.ROLLBACK_FAILED}
    assert current_target(env.overlay, "demo") is None
    assert env.store.read_expectation("demo") is None


async def test_kill_switch_refuses(env: Env) -> None:
    env.ex._config = env.ex._config.model_copy(update={"enabled": False})
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "apply_disabled"
    assert env.runner.calls == [] and env.fetcher.calls == []
    assert env.store.read_expectation("demo") is None


async def test_no_restart_command_refuses_before_any_change(env: Env) -> None:
    env.ex._config = env.ex._config.model_copy(update={"restart_command": []})
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "apply_disabled"
    assert env.runner.calls == [] and env.fetcher.calls == []
    assert current_target(env.overlay, "demo") is None


async def test_manual_rollback_returns_to_previous(env: Env) -> None:
    first = await env.ex.execute(env.run)
    assert first.state is RunState.SUCCEEDED
    back = env.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="1.2.0",
        to_version="1.1.0",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target=first.previous_target,
    )
    done = await env.ex.execute(back)
    assert (
        done.state is RunState.SUCCEEDED and current_target(env.overlay, "demo") is None
    )
    assert env.runner.restarts == 2
    assert not any("schema-init" in argv for argv, _ in env.runner.calls[2:])


async def test_rollback_run_to_a_verified_store_entry(env: Env) -> None:
    first = await env.ex.execute(env.run)
    assert first.state is RunState.SUCCEEDED
    down = env.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="1.2.0",
        to_version="1.1.0",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target=None,
    )
    assert (await env.ex.execute(down)).state is RunState.SUCCEEDED
    up = env.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target="demo-1.2.0",
    )
    done = await env.ex.execute(up)
    assert done.state is RunState.SUCCEEDED, done.message
    assert current_target(env.overlay, "demo") == "demo-1.2.0"


async def test_rollback_run_refuses_a_tampered_store_entry(env: Env) -> None:
    first = await env.ex.execute(env.run)
    assert first.state is RunState.SUCCEEDED
    down = env.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="1.2.0",
        to_version="1.1.0",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target=None,
    )
    assert (await env.ex.execute(down)).state is RunState.SUCCEEDED
    (env.overlay / ".store" / "demo-1.2.0" / "__init__.py").write_text("X = 666\n")
    up = env.store.create(
        kind=RunKind.ROLLBACK,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256=None,
        requested_by="bob",
        approval_required=False,
        approval_ttl_seconds=60,
        target="demo-1.2.0",
    )
    restarts = env.runner.restarts
    done = await env.ex.execute(up)
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert current_target(env.overlay, "demo") is None  # left on bundled
    assert env.runner.restarts == restarts
    assert env.store.read_expectation("demo") is None


async def test_redo_rollback_after_a_failed_one(env: Env) -> None:
    env.runner.boot_ok = [False, False, True]
    failed = await env.ex.execute(env.run)
    assert failed.state is RunState.ROLLBACK_FAILED
    again = await env.ex.redo_rollback(failed)
    assert again.state is RunState.ROLLED_BACK and again.failure == "health_failed"
    assert env.store.active("demo") is None
    assert env.store.read_expectation("demo") is None


class _Died(asyncio.CancelledError):
    """The updater is stopped while the restart command runs."""


async def test_resume_activation_after_the_updater_died(env: Env) -> None:
    real = env.runner.__call__

    def dies_on_restart(argv, *, env=None, timeout):  # type: ignore[no-untyped-def]
        if "schema-init" not in argv:
            raise _Died()
        return real(argv, env=env, timeout=timeout)

    env.ex._runner = dies_on_restart
    with pytest.raises(asyncio.CancelledError):
        await env.ex.execute(env.run)
    stuck = env.store.get(env.run.id)
    assert stuck is not None and stuck.state is RunState.ACTIVATING
    assert env.store.read_expectation("demo") is not None
    env.ex._runner = env.runner
    done = await env.ex.resume_activation(stuck)
    assert done.state is RunState.SUCCEEDED, done.message
    assert current_target(env.overlay, "demo") == "demo-1.2.0"
    assert env.store.read_expectation("demo") is None
