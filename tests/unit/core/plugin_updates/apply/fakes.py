"""Fakes for the executor: a release server, a command runner that 'boots' the API."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.plugin_updates.apply.boot_report import BootReport, PluginBootState
from core.plugin_updates.apply.models import Expectation
from core.plugin_updates.apply.store import RunStore
from core.plugins._overlay_guard import read_manifest_mapping


@dataclass
class FakeFetcher:
    meta: dict[str, Any]
    tarball_bytes: bytes
    calls: list[str] = field(default_factory=list)
    error: Exception | None = None
    tarball_error: Exception | None = None

    async def release_json(self, plugin: str, version: str) -> bytes:
        self.calls.append("release_json")
        if self.error is not None:
            raise self.error
        return json.dumps(self.meta).encode()

    async def tarball(self, plugin: str, version: str, dest: Path) -> None:
        self.calls.append("tarball")
        if self.tarball_error is not None:
            dest.write_bytes(b"partial")
            raise self.tarball_error
        dest.write_bytes(self.tarball_bytes)


def write_report(
    store: RunStore,
    name: str,
    plugins: dict[str, PluginBootState],
    *,
    booted_at: datetime | None = None,
) -> None:
    """Write one worker's boot report as the API would."""
    report = BootReport(
        pid=os.getpid(),
        booted_at=booted_at or datetime.now(UTC),
        core_version="1.50.0",
        plugins=plugins,
    )
    store.boot_dir.mkdir(parents=True, exist_ok=True)
    (store.boot_dir / f"{name}.json").write_text(report.model_dump_json())


@dataclass
class FakeRunner:
    """Records every command; a restart writes a boot report of what the link points at."""

    store: RunStore
    overlay: Path
    bundled: Path
    plugin: str = "demo"
    schema_rc: int = 0
    restart_rc: int = 0
    #: Per-restart exit codes, consumed first (then ``restart_rc`` applies).
    restart_rcs: list[int] = field(default_factory=list)
    boot_ok: list[bool] = field(default_factory=lambda: [True, True, True])
    #: Per-boot: does ``auth`` come up? (default: yes)
    auth_ok: list[bool] = field(default_factory=list)
    calls: list[tuple[list[str], dict[str, str] | None]] = field(default_factory=list)
    #: (expectation on disk, wall clock) at the moment each restart was issued.
    at_restart: list[tuple[Expectation | None, datetime]] = field(default_factory=list)

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> int:
        self.calls.append((list(argv), dict(env) if env is not None else None))
        if "schema-init" in argv:
            return self.schema_rc
        self.at_restart.append(
            (self.store.read_expectation(self.plugin), datetime.now(UTC))
        )
        rc = self.restart_rcs.pop(0) if self.restart_rcs else self.restart_rc
        if rc:
            return rc
        ok = self.boot_ok.pop(0) if self.boot_ok else True
        auth = self.auth_ok.pop(0) if self.auth_ok else True
        link = self.overlay / self.plugin
        directory = (
            str(link.resolve())
            if link.is_symlink()
            else str(self.bundled / self.plugin)
        )
        version = (
            Path(directory).name.rpartition("-")[2]
            if link.is_symlink()
            else str((read_manifest_mapping(Path(directory)) or {}).get("version"))
        )
        plugins = {
            self.plugin: PluginBootState(
                version=version, directory=directory, active=ok, healthy=ok
            ),
        }
        if auth:
            plugins["auth"] = PluginBootState(
                version="3.0.0", directory=str(self.bundled / "auth"), active=True
            )
        write_report(self.store, f"{len(self.calls)}", plugins)
        return 0

    @property
    def restarts(self) -> int:
        return sum("schema-init" not in argv for argv, _ in self.calls)
