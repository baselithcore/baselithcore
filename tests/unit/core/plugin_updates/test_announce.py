"""An update is announced once, whichever worker or replica sees it first."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates import announce
from core.plugin_updates import service as svc_mod
from core.plugin_updates.announce import (
    REDIS_KEY_PREFIX,
    AnnouncementGate,
    build_announcement_gate,
)
from core.plugin_updates.models import CheckReport, UpdateCandidate

REPO_ROOT = Path(__file__).resolve().parents[4]

_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    from core.plugin_updates.announce import AnnouncementGate
    root, key, go, ready = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    (ready / str(os.getpid())).touch()
    deadline = time.monotonic() + 60
    while not go.exists():
        if time.monotonic() > deadline:
            raise SystemExit(2)
        time.sleep(0.002)
    print("CLAIM=" + ("1" if AnnouncementGate(root).claim_file(key) else "0"))
    """
)


def test_only_one_process_claims(tmp_path: Path) -> None:
    go, ready = tmp_path / "go", tmp_path / "ready"
    ready.mkdir()
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CHILD,
                str(tmp_path / "cache"),
                "demo@1.2.0",
                str(go),
                str(ready),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    deadline = time.monotonic() + 90
    while len(list(ready.iterdir())) < len(procs):
        assert time.monotonic() < deadline, "children never became ready"
        time.sleep(0.05)
    go.touch()
    outputs = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        claims = [line for line in out.splitlines() if line.startswith("CLAIM=")]
        assert claims, err  # import logs may share stdout; the marker line decides
        outputs.append(claims[-1])
    assert sorted(outputs) == ["CLAIM=0"] * 5 + ["CLAIM=1"]


def test_claim_file_is_once_per_key(tmp_path: Path) -> None:
    gate = AnnouncementGate(tmp_path)
    assert gate.claim_file("demo@1.2.0") is True
    assert gate.claim_file("demo@1.2.0") is False
    assert gate.claim_file("demo@1.3.0") is True
    assert AnnouncementGate(tmp_path).claim_file("system:0.41.0") is True


class _FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.store: dict[str, object] = {}
        self.calls: list[tuple[str, bool, int | None]] = []
        self.deleted: list[str] = []
        self.fail = fail

    async def set(
        self, name: str, value: object, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        self.calls.append((name, nx, ex))
        if self.fail:
            raise ConnectionError("redis down")
        if nx and name in self.store:
            return None
        self.store[name] = value
        return True

    async def exists(self, name: str) -> int:
        if self.fail:
            raise ConnectionError("redis down")
        return int(name in self.store)

    async def delete(self, name: str) -> int:
        self.deleted.append(name)
        return int(self.store.pop(name, None) is not None)


async def test_redis_claim_is_set_nx_with_ttl(tmp_path: Path) -> None:
    redis = _FakeRedis()
    gate = AnnouncementGate(tmp_path, redis_factory=lambda: redis, ttl_seconds=60)
    assert await gate.claim("demo@1.2.0") is True
    assert await gate.claim("demo@1.2.0") is False
    assert (f"{REDIS_KEY_PREFIX}claim:demo@1.2.0", True, 600) in redis.calls
    assert not (tmp_path / announce.LOCK_DIRNAME).exists()


async def test_redis_error_falls_back_to_lock_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    gate = AnnouncementGate(tmp_path, redis_factory=lambda: _FakeRedis(fail=True))
    with caplog.at_level("WARNING"):
        assert await gate.claim("demo@1.2.0") is True
        assert await gate.claim("demo@1.2.0") is False
    assert "plugin_update_announce_redis_failed: ConnectionError" in caplog.text


async def test_unwritable_cache_announces_rather_than_loses(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with caplog.at_level("WARNING"):
        assert await AnnouncementGate(blocker).claim("demo@1.2.0") is True
    assert "plugin_update_announce_lock_failed" in caplog.text


def test_build_gate_uses_redis_only_when_the_backend_is_redis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import core.config as config_pkg

    def storage(backend: str, url: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(cache_backend=backend, cache_redis_url=url)

    monkeypatch.setattr(
        config_pkg, "get_storage_config", lambda: storage("local", "redis://x")
    )
    assert build_announcement_gate(tmp_path)._redis_factory is None
    monkeypatch.setattr(config_pkg, "get_storage_config", lambda: storage("redis", ""))
    assert build_announcement_gate(tmp_path)._redis_factory is None
    monkeypatch.setattr(
        config_pkg, "get_storage_config", lambda: storage("redis", "redis://x")
    )
    assert build_announcement_gate(tmp_path)._redis_factory is not None


def _cfg(tmp: Path) -> PluginUpdateConfig:
    src = tmp / "m.yaml"
    src.write_text("mirrors:\n  demo:\n    repo: git@github.com:o/r.git\n")
    return PluginUpdateConfig(
        sources_file=src, cache_dir=tmp / "c", core_update_repo=""
    )


async def test_two_services_racing_emit_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[str] = []

    class _Bus:
        async def emit(self, name: str, data: dict | None = None, **_: object) -> int:
            await asyncio.sleep(0)  # yield: the other worker runs before we save
            emitted.append(name)
            return 0

    async def fake(*a: object, **k: object) -> CheckReport:
        return CheckReport(
            checked_at=datetime.now(UTC),
            candidates=[
                UpdateCandidate(
                    plugin="demo",
                    installed_version="1.0.0",
                    latest=None,
                    available=True,
                )
            ],
        )

    monkeypatch.setattr(svc_mod, "get_event_bus", lambda: _Bus())
    monkeypatch.setattr(svc_mod, "run_check", fake)
    cfg = _cfg(tmp_path)
    a = svc_mod.PluginUpdateService(
        cfg, bundled_root=tmp_path, gate=AnnouncementGate(cfg.cache_dir)
    )
    b = svc_mod.PluginUpdateService(
        cfg, bundled_root=tmp_path, gate=AnnouncementGate(cfg.cache_dir)
    )
    await asyncio.gather(a.check_now(), b.check_now())
    assert emitted == ["plugin.update_available"]


def _lock_of(gate: AnnouncementGate, key: str) -> Path:
    return gate._paths(key)[0]


def test_stale_lock_is_reclaimed_by_one(tmp_path: Path) -> None:
    gate = AnnouncementGate(tmp_path, lease_seconds=600)
    assert gate.claim_file("demo@1.2.0") is True
    old = time.time() - 3600
    os.utime(_lock_of(gate, "demo@1.2.0"), (old, old))
    assert gate.claim_file("demo@1.2.0") is True  # takeover
    assert gate.claim_file("demo@1.2.0") is False  # fresh lease again


async def test_done_marker_blocks_reannounce(tmp_path: Path) -> None:
    gate = AnnouncementGate(tmp_path)
    assert await gate.claim("demo@1.2.0") is True
    await gate.commit("demo@1.2.0")
    assert await gate.is_done("demo@1.2.0") is True
    assert await AnnouncementGate(tmp_path).claim("demo@1.2.0") is False
    # the marker, not a lease, blocks: no lock file remains
    assert not _lock_of(gate, "demo@1.2.0").exists()


async def test_release_lets_the_next_claim_win(tmp_path: Path) -> None:
    gate = AnnouncementGate(tmp_path)
    assert await gate.claim("demo@1.2.0") is True
    await gate.release("demo@1.2.0")
    assert await gate.claim("demo@1.2.0") is True


async def test_redis_commit_and_release(tmp_path: Path) -> None:
    redis = _FakeRedis()
    gate = AnnouncementGate(tmp_path, redis_factory=lambda: redis, ttl_seconds=99)
    assert await gate.claim("a@1") is True
    await gate.release("a@1")
    assert await gate.claim("a@1") is True
    await gate.commit("a@1")
    assert (f"{REDIS_KEY_PREFIX}done:a@1", False, 99) in redis.calls
    assert await gate.claim("a@1") is False
    assert await gate.is_done("a@1") is True


async def test_namespaces_do_not_suppress_each_other(tmp_path: Path) -> None:
    redis = _FakeRedis()
    one = AnnouncementGate(
        tmp_path / "1", redis_factory=lambda: redis, namespace="a.example"
    )
    two = AnnouncementGate(
        tmp_path / "2", redis_factory=lambda: redis, namespace="b.example"
    )
    assert await one.claim("demo@1.2.0") is True
    assert await two.claim("demo@1.2.0") is True
    same = AnnouncementGate(tmp_path / "1", namespace="a.example")
    other = AnnouncementGate(tmp_path / "1", namespace="b.example")
    assert same.claim_file("k") is True
    assert other.claim_file("k") is True


def test_namespace_from_instance_id_then_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_BASE_URL", "https://Prod.Example.com/app")
    assert announce._namespace("") == "prod.example.com"
    assert announce._namespace(" eu 1 ") == "eu_1"
    monkeypatch.delenv("APP_BASE_URL")
    assert announce._namespace("") == ""


async def test_emit_failure_releases_and_next_check_announces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[str] = []
    fail = {"on": True}

    class _Bus:
        async def emit(self, name: str, data: dict | None = None, **_: object) -> int:
            if fail["on"]:
                raise RuntimeError("bus down")
            emitted.append(name)
            return 0

    async def fake(*a: object, **k: object) -> CheckReport:
        return CheckReport(
            checked_at=datetime.now(UTC),
            candidates=[
                UpdateCandidate(
                    plugin="demo",
                    installed_version="1.0.0",
                    latest=None,
                    available=True,
                )
            ],
        )

    monkeypatch.setattr(svc_mod, "get_event_bus", lambda: _Bus())
    monkeypatch.setattr(svc_mod, "run_check", fake)
    cfg = _cfg(tmp_path)
    svc = svc_mod.PluginUpdateService(
        cfg, bundled_root=tmp_path, gate=AnnouncementGate(cfg.cache_dir)
    )
    await svc.check_now()
    assert emitted == []
    fail["on"] = False
    await svc.check_now()
    await svc.check_now()
    assert emitted == ["plugin.update_available"]


_TAKEOVER_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path
    from core.plugin_updates.announce import AnnouncementGate
    root, key, go, ready = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    gate = AnnouncementGate(root, lease_seconds=600)
    (ready / str(os.getpid())).touch()
    deadline = time.monotonic() + 60
    while not go.exists():
        if time.monotonic() > deadline:
            raise SystemExit(2)
        time.sleep(0.001)
    print("CLAIM=" + ("1" if gate.claim_file(key) else "0"))
    """
)


def test_only_one_process_takes_over_a_stale_lock(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    seed = AnnouncementGate(cache, lease_seconds=600)
    assert seed.claim_file("demo@1.2.0") is True
    old = time.time() - 3600
    os.utime(seed._paths("demo@1.2.0")[0], (old, old))
    go, ready = tmp_path / "go", tmp_path / "ready"
    ready.mkdir()
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _TAKEOVER_CHILD,
                str(cache),
                "demo@1.2.0",
                str(go),
                str(ready),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    deadline = time.monotonic() + 90
    while len(list(ready.iterdir())) < len(procs):
        assert time.monotonic() < deadline, "children never became ready"
        time.sleep(0.05)
    go.touch()
    outputs = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        claims = [line for line in out.splitlines() if line.startswith("CLAIM=")]
        assert claims, err
        outputs.append(claims[-1])
    assert sorted(outputs) == ["CLAIM=0"] * 7 + ["CLAIM=1"]


def test_takeover_is_refused_when_the_lock_was_just_replaced(tmp_path: Path) -> None:
    gate = AnnouncementGate(tmp_path, lease_seconds=600)
    assert gate.claim_file("k") is True
    lock = gate._paths("k")[0]
    old = time.time() - 3600
    os.utime(lock, (old, old))
    real_stat = Path.stat
    calls = {"n": 0}

    def stat(self: Path, *a: object, **k: object) -> os.stat_result:
        result = real_stat(self, *a, **k)
        if self == lock:
            calls["n"] += 1
            if calls["n"] == 1:  # the first look sees it stale, then a rival wins
                fresh = time.time()
                os.utime(lock, (fresh, fresh))
                return result
        return result

    Path.stat = stat  # type: ignore[method-assign]
    try:
        assert gate.claim_file("k") is False
    finally:
        Path.stat = real_stat  # type: ignore[method-assign]


def test_stale_takeover_guard_is_cleared_after_a_crash(tmp_path: Path) -> None:
    gate = AnnouncementGate(tmp_path, lease_seconds=600)
    assert gate.claim_file("k") is True
    lock = gate._paths("k")[0]
    guard = lock.with_name(f"{lock.name}.takeover")
    guard.write_text("x")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    os.utime(guard, (old, old))
    assert gate.claim_file("k") is False  # clears the dead guard
    assert gate.claim_file("k") is True


def test_empty_namespace_with_redis_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import core.config as config_pkg

    monkeypatch.delenv("APP_BASE_URL", raising=False)
    monkeypatch.setattr(announce, "_warned_empty_namespace", False)
    monkeypatch.setattr(
        config_pkg,
        "get_storage_config",
        lambda: types.SimpleNamespace(
            cache_backend="redis", cache_redis_url="redis://x"
        ),
    )
    with caplog.at_level("WARNING"):
        build_announcement_gate(tmp_path)
        build_announcement_gate(tmp_path)
        build_announcement_gate(tmp_path, "eu-1")
    assert caplog.text.count("plugin_update_announce_namespace_empty") == 1
