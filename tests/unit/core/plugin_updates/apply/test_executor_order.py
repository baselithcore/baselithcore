"""The live link moves only right before the restart; schema-init sees a scratch overlay.

The running API resolves ``plugins.<name>`` through ``<overlay>/<name>`` for
lazy submodule imports, on-demand activation and respawned workers. While
``schema-init`` runs (up to ten minutes) that link must still point at the
code the API booted with, and a failed ``schema-init`` must leave the running
API exactly as it was.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from core.plugin_updates.apply.models import RunState
from core.plugin_updates.apply.reconcile import reconcile
from core.plugin_updates.apply.swap import current_target, point_to
from core.plugins.overlay import OVERLAY_ENV

from ._executor_env import Env


class _Died(asyncio.CancelledError):
    """The updater is stopped at a chosen point."""


def _spy_schema(env: Env) -> list[tuple[str | None, str, bool, str | None]]:
    """Wrap the runner: what the live link and the scratch overlay held during schema-init."""
    seen: list[tuple[str | None, str, bool, str | None]] = []
    real = env.runner.__call__

    def runner(
        argv: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: float
    ) -> int:
        if "schema-init" in argv:
            assert env is not None
            scratch = Path(env[OVERLAY_ENV])
            link = scratch / "demo"
            seen.append(
                (
                    current_target(overlay, "demo"),
                    str(scratch),
                    link.is_symlink(),
                    link.resolve().name if link.is_symlink() else None,
                )
            )
        return real(argv, env=env, timeout=timeout)

    overlay = env.overlay
    env.ex._runner = runner
    return seen


async def test_schema_init_runs_before_the_live_link_moves(env: Env) -> None:
    seen = _spy_schema(env)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.SUCCEEDED, done.message
    [(live, scratch, is_link, points_at)] = seen
    assert live is None  # the running API still resolves the bundled copy
    assert is_link and points_at == "demo-1.2.0"
    assert Path(scratch).parent == env.overlay / ".store"
    assert not Path(scratch).exists()  # removed afterwards
    assert current_target(env.overlay, "demo") == "demo-1.2.0"


async def test_scratch_overlay_carries_the_other_overlaid_plugins(env: Env) -> None:
    other = env.overlay / ".store" / "other-2.0.0"
    other.mkdir(parents=True)
    (other / "__init__.py").write_text("")
    os.symlink(os.path.join(".store", "other-2.0.0"), env.overlay / "other")
    views: list[str | None] = []
    real = env.runner.__call__

    def runner(argv, *, env=None, timeout):  # type: ignore[no-untyped-def]
        if "schema-init" in argv:
            views.append(os.readlink(Path(env[OVERLAY_ENV]) / "other"))
        return real(argv, env=env, timeout=timeout)

    env.ex._runner = runner
    assert (await env.ex.execute(env.run)).state is RunState.SUCCEEDED
    assert views == [os.path.join(".store", "other-2.0.0")]


async def test_migration_failure_never_touched_the_running_api(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    moves: list[str | None] = []

    def spy(overlay_root, plugin, target, *, run_id):  # type: ignore[no-untyped-def]
        moves.append(target)
        point_to(overlay_root, plugin, target, run_id=run_id)

    monkeypatch.setattr("core.plugin_updates.apply._activation.point_to", spy)
    env.runner.schema_rc = 3
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "migration_failed"
    assert moves == []  # no swap, no swap-back
    assert "nothing changed on the running API" in done.message
    assert env.runner.restarts == 0
    assert not list((env.overlay / ".store").glob(".staging-*"))


async def test_crash_after_schema_init_before_the_swap_resumes(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real_swap = env.ex._swap

    async def dies_first(run, target):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise _Died()
        return await real_swap(run, target)

    monkeypatch.setattr(env.ex, "_swap", dies_first)
    with pytest.raises(asyncio.CancelledError):
        await env.ex.execute(env.run)
    stuck = env.store.get(env.run.id)
    assert stuck is not None and stuck.state is RunState.ACTIVATING
    assert current_target(env.overlay, "demo") is None  # never switched
    schema_runs = sum("schema-init" in a for a, _ in env.runner.calls)
    await reconcile(env.store, env.ex)
    done = env.store.get(env.run.id)
    assert done is not None and done.state is RunState.SUCCEEDED, done.message
    assert current_target(env.overlay, "demo") == "demo-1.2.0"
    assert sum("schema-init" in a for a, _ in env.runner.calls) == schema_runs


async def test_crash_after_the_swap_before_the_restart_resumes(env: Env) -> None:
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
    assert current_target(env.overlay, "demo") == "demo-1.2.0"
    env.ex._runner = env.runner
    await reconcile(env.store, env.ex)
    done = env.store.get(env.run.id)
    assert done is not None and done.state is RunState.SUCCEEDED, done.message
    assert env.runner.restarts == 1


async def test_crash_during_schema_init_fails_without_touching_the_link(
    env: Env,
) -> None:
    real = env.runner.__call__

    def dies_on_schema(argv, *, env=None, timeout):  # type: ignore[no-untyped-def]
        if "schema-init" in argv:
            raise _Died()
        return real(argv, env=env, timeout=timeout)

    env.ex._runner = dies_on_schema
    with pytest.raises(asyncio.CancelledError):
        await env.ex.execute(env.run)
    stuck = env.store.get(env.run.id)
    assert stuck is not None and stuck.state is RunState.MIGRATING
    assert current_target(env.overlay, "demo") is None
    env.ex._runner = env.runner
    await reconcile(env.store, env.ex)
    done = env.store.get(env.run.id)
    assert done is not None and done.state is RunState.FAILED
    assert done.failure == "interrupted"
    assert current_target(env.overlay, "demo") is None and env.runner.restarts == 0
    assert not list((env.overlay / ".store").glob(".staging-*"))


async def test_undo_swap_only_moves_a_link_that_moved(env: Env) -> None:
    moved = await env.ex.execute(env.run)
    assert moved.state is RunState.SUCCEEDED
    # A MIGRATING run recorded by an older updater that had already switched.
    env.ex.undo_swap(moved)
    assert current_target(env.overlay, "demo") is None
    env.ex.undo_swap(moved)  # already back: a no-op, not an error
    assert current_target(env.overlay, "demo") is None


async def test_a_refused_switch_fails_without_a_restart(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_a, **_k):  # type: ignore[no-untyped-def]
        raise OSError("read-only overlay")

    monkeypatch.setattr("core.plugin_updates.apply._activation.point_to", refuse)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "overlay_refused"
    assert "schema changes from 1.2.0 remain" in done.message
    assert env.runner.restarts == 0 and env.store.active("demo") is None
    assert env.store.read_expectation("demo") is None
