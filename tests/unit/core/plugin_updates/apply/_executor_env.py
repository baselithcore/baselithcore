"""A bundled plugin, a signed 1.2.0 release, an overlay and an executor wired to fakes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugin_updates.apply.boot_report import PluginBootState
from core.plugin_updates.apply.executor import Executor
from core.plugin_updates.apply.models import ApplyRun, RunKind
from core.plugin_updates.apply.store import RunStore
from core.plugin_updates.cache import UpdateCache
from core.plugins.signing import generate_keypair_hex

from ..test_checker import _signed_tarball
from .fakes import FakeFetcher, FakeRunner, write_report


class Clock:
    """A monotonic clock that only moves when something sleeps on it."""

    t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


@dataclass
class Env:
    ex: Executor
    run: ApplyRun
    runner: FakeRunner
    fetcher: FakeFetcher
    store: RunStore
    overlay: Path
    bundled: Path
    cache: UpdateCache
    meta: dict[str, Any]
    priv: str
    tmp: Path


def build_env(tmp_path: Path, **config: Any) -> Env:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path / "rel", priv)
    bundled = tmp_path / "plugins"
    (bundled / "demo").mkdir(parents=True)
    (bundled / "demo" / "manifest.yaml").write_text("name: demo\nversion: 1.1.0\n")
    (bundled / "demo" / "__init__.py").write_text("X = 0\n")
    (bundled / "demo" / ".env").write_text("K=v\n")
    overlay = tmp_path / "ov"
    overlay.mkdir()
    cache = UpdateCache(tmp_path / "cache")
    cache.tarball_path("demo", "1.2.0").parent.mkdir(parents=True)
    cache.tarball_path("demo", "1.2.0").write_bytes(tgz.read_bytes())
    store = RunStore(tmp_path / "state")
    write_report(  # the API booted once with the updater on, before any run
        store,
        "pre",
        {"demo": PluginBootState(version="1.1.0", directory="x", active=True)},
        booted_at=datetime.now(UTC) - timedelta(hours=2),
    )
    owner = tmp_path / "owner.env"
    owner.write_text("POSTGRES_USER=owner\nPOSTGRES_PASSWORD=pw-owner-secret\n")
    cfg = UpdateApplyConfig(
        enabled=True,
        restart_command=["systemctl", "restart", "api"],
        schema_env_file=owner,
        state_dir=tmp_path / "state",
        **config,
    ).model_copy(update={"stable_seconds": 5, "health_timeout_seconds": 30})
    runner = FakeRunner(store=store, overlay=overlay, bundled=bundled)
    fetcher = FakeFetcher(meta=meta, tarball_bytes=tgz.read_bytes())
    clock = Clock()

    async def probe() -> int:
        return 200

    ex = Executor(
        store=store,
        config=cfg,
        overlay_root=overlay,
        bundled_root=bundled,
        cache=cache,
        fetcher=fetcher,
        runner=runner,
        probe=probe,
        trusted_keys=lambda: [pub],
        core_version="1.50.0",
        max_unpacked_bytes=10_000_000,
        clock=clock,
        sleep=clock.sleep,
        now=None,
    )
    run = store.create(
        kind=RunKind.UPDATE,
        plugin="demo",
        from_version="1.1.0",
        to_version="1.2.0",
        tarball_sha256=meta["tarball_sha256"],
        requested_by="alice",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    return Env(
        ex, run, runner, fetcher, store, overlay, bundled, cache, meta, priv, tmp_path
    )


def served_text(e: Env, run: ApplyRun) -> str:
    """Everything a run exposes to API callers: its snapshot and journal."""
    parts = [run.model_dump_json()]
    parts += [t.model_dump_json() for t in e.store.journal(run.id)]
    return "\n".join(parts)
