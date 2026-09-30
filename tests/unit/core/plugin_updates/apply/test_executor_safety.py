"""The executor's safety obligations: pin, locking, threads, pruning, what it exposes."""

from __future__ import annotations

import fcntl
import logging
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from core.plugin_updates.apply import executor as executor_mod
from core.plugin_updates.apply.models import RunState
from core.plugin_updates.release_manifest import sign_release_manifest
from core.plugin_updates.sources import SourceError

from ._executor_env import Env, served_text


def _resigned(e: Env, **changes: Any) -> dict[str, Any]:
    meta = {k: v for k, v in e.meta.items() if k != "manifest_signature_ed25519"}
    meta.update(changes)
    meta["manifest_signature_ed25519"] = sign_release_manifest(meta, e.priv)
    return meta


def _spy_stage(monkeypatch: pytest.MonkeyPatch, e: Env, seen: dict[str, Any]) -> None:
    real = executor_mod.stage_release

    def spy(**kwargs: Any) -> Any:
        seen["pinned"] = kwargs["pinned_sha256"]
        seen["thread"] = threading.current_thread()
        fd = os.open(e.store.root / "locks" / "demo.lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            seen["locked"] = False
        except BlockingIOError:
            seen["locked"] = True
        finally:
            os.close(fd)
        return real(**kwargs)

    monkeypatch.setattr(executor_mod, "stage_release", spy)


async def test_pin_from_the_run_reaches_staging_unchanged_off_loop_under_lock(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}
    _spy_stage(monkeypatch, env, seen)
    # Same release, hash spelled differently: only the run's own pin may reach staging.
    env.fetcher.meta = _resigned(env, tarball_sha256=env.meta["tarball_sha256"].upper())
    done = await env.ex.execute(env.run)
    assert done.state is RunState.SUCCEEDED, done.message
    assert seen["pinned"] == env.run.tarball_sha256
    assert seen["thread"] is not threading.main_thread()
    assert seen["locked"] is True


async def test_validly_signed_release_with_another_tarball_is_release_changed(
    env: Env,
) -> None:
    env.fetcher.meta = _resigned(env, tarball_sha256="0" * 64)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "release_changed"
    assert (
        env.runner.calls == [] and not (env.overlay / ".store" / "demo-1.2.0").exists()
    )


async def test_cached_tarball_differing_from_the_pin_is_refused(env: Env) -> None:
    env.cache.tarball_path("demo", "1.2.0").write_bytes(b"not the release")
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "artifact_checksum"
    assert env.runner.calls == []


async def test_missing_tarball_is_fetched_then_pinned(env: Env) -> None:
    env.cache.tarball_path("demo", "1.2.0").unlink()
    done = await env.ex.execute(env.run)
    assert done.state is RunState.SUCCEEDED, done.message
    assert env.fetcher.calls == ["release_json", "tarball"]
    assert not list(env.cache.tarball_path("demo", "1.2.0").parent.glob(".download-*"))


async def test_source_error_fails_without_urls(env: Env) -> None:
    env.fetcher.error = SourceError("GitHub returned HTTP 404", 404)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "verification_failed"
    assert "http" not in done.message.lower().replace("http 404", "")
    assert env.store.read_expectation("demo") is None


def _old_entries(overlay: Path) -> None:
    for version in ("1.0.0", "1.0.5"):
        entry = overlay / ".store" / f"demo-{version}"
        entry.mkdir(parents=True)
        (entry / "manifest.yaml").write_text(f"name: demo\nversion: {version}\n")


async def test_prune_only_after_a_healthy_verdict(env: Env) -> None:
    _old_entries(env.overlay)
    env.runner.boot_ok = [False, True]
    done = await env.ex.execute(env.run)
    assert done.state is RunState.ROLLED_BACK
    store = env.overlay / ".store"
    assert {p.name for p in store.iterdir() if p.is_dir()} >= {
        "demo-1.0.0",
        "demo-1.0.5",
        "demo-1.2.0",
    }


async def test_prune_after_success_keeps_live_and_newest(env: Env) -> None:
    _old_entries(env.overlay)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.SUCCEEDED
    names = {p.name for p in (env.overlay / ".store").iterdir() if p.is_dir()}
    assert "demo-1.2.0" in names and "demo-1.0.0" not in names


async def test_expectation_is_written_before_the_restart_same_clock(env: Env) -> None:
    await env.ex.execute(env.run)
    expect, issued_at = env.runner.at_restart[0]
    assert expect is not None and expect.run_id == env.run.id
    assert expect.version == "1.2.0" and expect.store_dir == "demo-1.2.0"
    assert expect.restart_at.tzinfo is not None and expect.restart_at <= issued_at


@pytest.mark.parametrize(
    ("setup", "state"),
    [
        (lambda e: setattr(e.runner, "schema_rc", 2), RunState.FAILED),
        (lambda e: setattr(e.runner, "boot_ok", [False, True]), RunState.ROLLED_BACK),
        (
            lambda e: setattr(e.runner, "boot_ok", [False, False]),
            RunState.ROLLBACK_FAILED,
        ),
        (lambda e: setattr(e.runner, "restart_rc", 1), RunState.ROLLBACK_FAILED),
        (
            lambda e: e.cache.tarball_path("demo", "1.2.0").write_bytes(b"x"),
            RunState.FAILED,
        ),
    ],
)
async def test_failures_expose_no_host_path_and_clear_the_expectation(
    env: Env, setup: Any, state: RunState
) -> None:
    setup(env)
    done = await env.ex.execute(env.run)
    assert done.state is state
    text = served_text(env, done)
    assert str(env.tmp) not in text and str(env.tmp.resolve()) not in text
    assert "/.store/" not in text
    assert env.store.read_expectation("demo") is None


async def test_owner_credentials_are_never_logged_or_stored(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    env.runner.schema_rc = 1
    with caplog.at_level(logging.DEBUG):
        done = await env.ex.execute(env.run)
    assert "pw-owner-secret" not in caplog.text
    assert "pw-owner-secret" not in served_text(env, done)


async def test_a_live_link_that_escapes_is_refused_without_its_path(env: Env) -> None:
    (env.overlay / "demo").symlink_to(env.tmp)
    done = await env.ex.execute(env.run)
    assert done.state is RunState.FAILED and done.failure == "overlay_refused"
    assert str(env.tmp) not in served_text(env, done)
    assert (
        env.overlay / "demo"
    ).resolve() == env.tmp.resolve()  # left for the operator
    assert env.runner.calls == []
