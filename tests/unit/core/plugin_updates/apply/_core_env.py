"""A fake checkout, command runner and release server around a CoreHostDriver."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates.apply.boot_report import BootReport, PluginBootState
from baselith.core.plugin_updates.apply.drivers._git import GitResult
from baselith.core.plugin_updates.apply.drivers.core_host import CoreHostDriver
from baselith.core.plugin_updates.apply.models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    ApplyRun,
    RunKind,
)
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.distribution_manifest import (
    DistributionManifest,
    build_manifest_meta,
)
from baselith.core.plugin_updates.release_manifest import (
    SIGNATURE_KEY,
    sign_release_manifest,
)
from baselith.core.plugins.signing import generate_keypair_hex

from ._executor_env import Clock

NAME = "baselithcore-enterprise"
OLD = "1" * 40
NEW = "2" * 40
LOCK = b"version = 1\n"
VERSIONS = {OLD: "1.14.0", NEW: "1.15.0"}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class FakeGit:
    """A checkout: HEAD, the commits present, the ones a fetch can bring, their locks."""

    root: Path
    log: list[str]
    head: str = OLD
    local: set[str] = field(default_factory=lambda: {OLD})
    remote: set[str] = field(default_factory=lambda: {OLD, NEW})
    locks: dict[str, bytes] = field(default_factory=lambda: {OLD: LOCK, NEW: LOCK})
    status: str = ""
    checkout_fails: set[str] = field(default_factory=set)
    calls: list[list[str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        (self.root / "uv.lock").write_bytes(self.locks[self.head])

    def __call__(self, argv: Sequence[str], *, cwd: Path, timeout: float) -> GitResult:
        args = list(argv)
        self.calls.append(args)
        assert cwd == self.root
        if args[:3] == ["rev-parse", "--verify", "HEAD^{commit}"]:
            return GitResult(0, f"{self.head}\n")
        if args[:3] == ["rev-parse", "--verify", "--quiet"]:
            wanted = args[3].removesuffix("^{commit}")
            return (
                GitResult(0, f"{wanted}\n")
                if wanted in self.local
                else GitResult(1, "")
            )
        if args[:1] == ["status"]:
            return GitResult(0, self.status)
        if args[:1] == ["fetch"]:
            self.log.append("git fetch")
            wanted = args[-1]
            if wanted in self.remote:
                self.local.add(wanted)
            return GitResult(0, "")
        if args[:1] == ["checkout"]:
            target = args[-1]
            self.log.append(f"git checkout {target[:4]}")
            if target not in self.local or target in self.checkout_fails:
                return GitResult(1, "")
            self.head = target
            (self.root / "uv.lock").write_bytes(self.locks[target])
            return GitResult(0, "")
        return GitResult(1, "")

    @property
    def writes(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] in ("fetch", "checkout")]


@dataclass
class FakeRunner:
    """Backup, migrations and restarts; a restart 'boots' the API on HEAD's version."""

    store: RunStore
    git: FakeGit
    log: list[str]
    backup_rc: int = 0
    migrate_rc: int = 0
    schema_rc: int = 0
    restart_rcs: list[int] = field(default_factory=list)
    #: Per boot: does the API come up on HEAD's version? (default: yes)
    boot_ok: list[bool] = field(default_factory=list)
    plugins: tuple[str, ...] = ("auth",)
    calls: list[tuple[list[str], dict[str, str] | None]] = field(default_factory=list)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> int:
        args = list(argv)
        self.calls.append((args, dict(env) if env is not None else None))
        if args[:1] == ["/usr/local/bin/backup"]:
            self.log.append("backup")
            return self.backup_rc
        if args[-2:] == ["db", "migrate"]:
            self.log.append("db migrate")
            return self.migrate_rc
        if args[-1] == "schema-init":
            self.log.append("schema-init")
            return self.schema_rc
        self.log.append("restart")
        rc = self.restart_rcs.pop(0) if self.restart_rcs else 0
        if rc:
            return rc
        ok = self.boot_ok.pop(0) if self.boot_ok else True
        version = VERSIONS[self.git.head] if ok else "0.0.0"
        report = BootReport(
            pid=os.getpid(),
            booted_at=datetime.now(UTC),
            core_version="1.3.1",
            framework_version=version,
            plugins={
                n: PluginBootState(
                    version="1", directory="d", active=True, healthy=True
                )
                for n in self.plugins
            },
        )
        self.store.boot_dir.mkdir(parents=True, exist_ok=True)
        (self.store.boot_dir / f"{len(self.calls)}.json").write_text(
            report.model_dump_json()
        )
        return 0

    @property
    def restarts(self) -> int:
        return self.log.count("restart")


@dataclass
class FakeDistFetcher:
    raw: bytes
    error: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def manifest(self, version: str) -> bytes:
        self.calls.append(version)
        if self.error is not None:
            raise self.error
        return self.raw


@dataclass
class CoreEnv:
    driver: CoreHostDriver
    run: ApplyRun
    store: RunStore
    git: FakeGit
    runner: FakeRunner
    fetcher: FakeDistFetcher
    log: list[str]
    priv: str
    tmp: Path


def signed_manifest(priv: str, **over: Any) -> bytes:
    fields: dict[str, Any] = {
        "name": NAME,
        "version": "1.15.0",
        "commit_sha": NEW,
        "lock_sha256": sha(LOCK),
        "migrations": "none",
    }
    fields.update(over)
    meta = build_manifest_meta(DistributionManifest(**fields))
    return json.dumps(
        {**meta, SIGNATURE_KEY: sign_release_manifest(meta, priv)}
    ).encode()


def create_run(
    store: RunStore, *, kind: RunKind = RunKind.UPDATE, pin: str = NEW
) -> ApplyRun:
    return store.create(
        kind=kind,
        plugin=CORE_CLAIM,
        subject=CORE_SUBJECT,
        from_version="1.14.0",
        to_version="1.15.0" if kind is RunKind.UPDATE else "1.14.0",
        tarball_sha256=pin,
        requested_by="cli:op",
        approval_required=False,
        approval_ttl_seconds=60,
    )


def build_core_env(tmp_path: Path, *, pin: str = NEW, **config: Any) -> CoreEnv:
    priv, pub = generate_keypair_hex()
    root = tmp_path / "checkout"
    root.mkdir()
    store = RunStore(tmp_path / "state")
    log: list[str] = []
    git = FakeGit(root=root, log=log)
    runner = FakeRunner(store=store, git=git, log=log)
    fetcher = FakeDistFetcher(raw=signed_manifest(priv))
    owner = tmp_path / "owner.env"
    owner.write_text("POSTGRES_USER=owner\nPOSTGRES_PASSWORD=pw-owner-secret\n")
    owner.chmod(0o600)
    settings: dict[str, Any] = {
        "enabled": True,
        "core_enabled": True,
        "restart_command": ["systemctl", "restart", "api"],
        "backup_command": ["/usr/local/bin/backup"],
        "updater_restart_command": ["systemctl", "--no-block", "restart", "updater"],
        "schema_env_file": owner,
        "state_dir": tmp_path / "state",
    }
    settings.update(config)
    cfg = UpdateApplyConfig(**settings).model_copy(
        update={"stable_seconds": 5, "health_timeout_seconds": 30}
    )
    report = BootReport(  # the API booted once with the updater on, before any run
        pid=1,
        booted_at=datetime.now(UTC) - timedelta(hours=2),
        core_version="1.3.1",
        framework_version="1.14.0",
        plugins={"auth": PluginBootState(version="1", directory="d", active=True)},
    )
    store.boot_dir.mkdir(parents=True, exist_ok=True)
    (store.boot_dir / "pre.json").write_text(report.model_dump_json())
    clock = Clock()

    async def probe() -> int:
        return 200

    driver = CoreHostDriver(
        store=store,
        config=cfg,
        git=git,
        runner=runner,
        fetcher=fetcher,
        probe=probe,
        trusted_keys=lambda name: [pub] if name == NAME else [],
        distribution_name=NAME,
        repo_root=root,
        core_version="1.3.1",
        framework_version="1.14.0",
        clock=clock,
        sleep=clock.sleep,
    )
    run = create_run(store, pin=pin)
    return CoreEnv(driver, run, store, git, runner, fetcher, log, priv, tmp_path)


def states(env: CoreEnv, run_id: str) -> list[str]:
    return [t.state.value for t in env.store.journal(run_id)]
