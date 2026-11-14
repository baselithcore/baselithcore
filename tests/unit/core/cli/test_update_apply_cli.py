"""``baselith update apply core`` / ``rollback core``: a pre-approved, pinned core run."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from baselith.core import _version
from baselith.core.cli import handlers
from baselith.core.cli.commands import _update_apply
from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates._core_state import release_name
from baselith.core.plugin_updates.apply.models import (
    CORE_CLAIM,
    CORE_SUBJECT,
    RunKind,
    RunState,
)
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.distribution_manifest import (
    DistributionManifest,
    build_manifest_meta,
)
from baselith.core.plugin_updates.models import CheckReport, ReleaseInfo, SystemUpdate
from baselith.core.plugin_updates.preflight import PreflightItem
from baselith.core.plugin_updates.release_manifest import (
    SIGNATURE_KEY,
    sign_release_manifest,
)
from baselith.core.plugins.signing import generate_keypair_hex

from .test_update_cli import _root

COMMIT = "c" * 40
NAME = release_name()


class _Fetcher:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    async def manifest(self, version: str) -> bytes:
        return self.raw


def _manifest(priv: str, version: str = "9.9.9") -> bytes:
    meta = build_manifest_meta(
        DistributionManifest(
            name=NAME,
            version=version,
            commit_sha=COMMIT,
            lock_sha256="d" * 64,
            migrations="none",
        )
    )
    return json.dumps(
        {**meta, SIGNATURE_KEY: sign_release_manifest(meta, priv)}
    ).encode()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    priv, pub = generate_keypair_hex()
    store = RunStore(tmp_path / "state")
    cfg = UpdateApplyConfig(state_dir=tmp_path / "state")
    notice = SystemUpdate(
        component="distribution",
        repo="acme/dist",
        installed_version=_version.__version__,
        latest=ReleaseInfo(
            plugin="core", version="9.9.9", tag="v9.9.9", published_at=None
        ),
        available=True,
    )
    report = CheckReport(
        checked_at=datetime.now(UTC), candidates=[], system=notice, distribution=notice
    )
    monkeypatch.setattr(_update_apply, "load_served_report", lambda: report)
    monkeypatch.setattr(_update_apply, "get_update_apply_config", lambda: cfg)
    monkeypatch.setattr(
        "baselith.core.plugins.signing.load_trust_roots", lambda name=None: [pub]
    )
    seen: dict[str, Any] = {
        "items": [PreflightItem(code="ready", level="ok", message="ok")]
    }

    def preflight(self, subject: str, version: str | None = None, core=None):
        seen.update(subject=subject, version=version, core=core)
        return seen["items"]

    monkeypatch.setattr(_update_apply.PluginUpdateService, "preflight", preflight)
    return {
        "store": store,
        "priv": priv,
        "seen": seen,
        "fetcher": _Fetcher(_manifest(priv)),
    }


def _apply(
    env: dict[str, Any], *, yes: bool = True, version: str = "9.9.9", **kw: Any
) -> int:
    return _update_apply.run_apply_core(
        version,
        yes=yes,
        fetcher=env["fetcher"],
        store=env["store"],
        **kw,
    )


def test_apply_creates_a_preapproved_core_run(env: dict[str, Any], capsys) -> None:
    assert _apply(env) == 0
    run_id = capsys.readouterr().out.strip().splitlines()[-1]
    run = env["store"].get(run_id)
    assert run is not None and run.state is RunState.APPROVED
    assert (run.plugin, run.subject, run.kind) == (
        CORE_CLAIM,
        CORE_SUBJECT,
        RunKind.UPDATE,
    )
    assert run.tarball_sha256 == COMMIT and run.to_version == "9.9.9"
    assert run.from_version == _version.__version__
    assert run.requested_by.startswith("cli:")
    core = env["seen"]["core"]
    assert core.release_checked and core.release.commit_sha == COMMIT


def test_apply_refuses_when_preflight_blocks(env: dict[str, Any], capsys) -> None:
    env["seen"]["items"] = [
        PreflightItem(code="blocker_tree_dirty", level="block", message="dirty")
    ]
    assert _apply(env) == 1
    out = capsys.readouterr().out
    assert "[block] blocker_tree_dirty: dirty" in out
    assert env["store"].active(CORE_CLAIM) is None


def test_apply_refuses_an_unsigned_manifest(env: dict[str, Any], capsys) -> None:
    env["fetcher"] = _Fetcher(_manifest(generate_keypair_hex()[0]))  # not trusted
    assert _apply(env) == 1
    core = env["seen"]["core"]
    assert core.release is None and "signature_invalid" in core.release_detail
    assert env["store"].active(CORE_CLAIM) is None


def test_apply_refuses_a_version_that_is_not_the_latest(
    env: dict[str, Any], capsys
) -> None:
    assert _apply(env, version="9.9.8") == 1
    assert "not the latest known release (9.9.9)" in capsys.readouterr().out
    assert "core" not in env["seen"]  # nothing fetched, nothing judged


def test_yes_flag_skips_the_prompt(env: dict[str, Any]) -> None:
    def never(_q: str) -> str:
        raise AssertionError("prompted")

    assert _apply(env, yes=True, prompt=never) == 0


def test_declined_prompt_creates_nothing(env: dict[str, Any], capsys) -> None:
    questions: list[str] = []
    assert _apply(env, yes=False, prompt=lambda q: questions.append(q) or "n") == 1
    assert questions and COMMIT[:12] in questions[0]
    assert env["store"].active(CORE_CLAIM) is None
    assert _apply(env, yes=False, prompt=lambda q: "y") == 0


def test_rollback_without_a_previous_core_run_exits_1(
    env: dict[str, Any], capsys
) -> None:
    rc = _update_apply.run_rollback_core(yes=True, store=env["store"])
    assert rc == 1 and "no successful one-click update" in capsys.readouterr().out


def test_rollback_pins_the_replaced_commit(
    env: dict[str, Any], capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    store: RunStore = env["store"]
    done = store.create(
        kind=RunKind.UPDATE,
        plugin=CORE_CLAIM,
        subject=CORE_SUBJECT,
        from_version="1.14.0",
        to_version="9.9.9",
        tarball_sha256=COMMIT,
        requested_by="cli:op",
        approval_required=False,
        approval_ttl_seconds=60,
    )
    store.transition(done.id, RunState.MIGRATING, previous_target="b" * 40)
    store.transition(done.id, RunState.SUCCEEDED)
    monkeypatch.setattr(_update_apply, "head_sha", lambda git, root: COMMIT)
    assert _update_apply.run_rollback_core(yes=True, store=store) == 0
    run = store.get(capsys.readouterr().out.strip().splitlines()[-1])
    assert run is not None and run.kind is RunKind.ROLLBACK
    assert run.tarball_sha256 == "b" * 40 and run.to_version == "1.14.0"


def test_plugin_subjects_are_pointed_at_plugin_updater(capsys) -> None:
    args = _root().parse_args(["update", "apply", "aura", "--version", "1.0.0"])
    assert handlers.cmd_update(args) == 2
    assert "plugin-updater request" in capsys.readouterr().out


def test_help_text_no_longer_says_installs_nothing() -> None:
    text = (
        _root().format_help()
        + _root()
        ._subparsers._group_actions[0]
        .choices[  # type: ignore[union-attr]
            "update"
        ]
        .format_help()
    )
    assert "installs nothing" not in text and "Nothing is installed" not in text
    assert "apply" in text and "rollback" in text
