"""``baselith update policy get|set``, ``update runs`` and ``update resolve``."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

import baselith.core.cli.__main__ as cli_main
from baselith.core.cli import handlers
from baselith.core.cli.commands import _update_policy, plugin_updater
from baselith.core.cli.commands.update import register_parser
from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.plugin_updates.apply.models import RunKind, RunState
from baselith.core.plugin_updates.apply.store import RunStore
from baselith.core.plugin_updates.policy import POLICY_OFF

SET_ON = [
    "update",
    "policy",
    "set",
    "--core",
    "patch",
    "--plugins",
    "patch",
    "--allow",
    "aura",
    "--allow",
    "wikigen",
    "--days",
    "sat,sun",
    "--start",
    "01:30",
    "--end",
    "05:00",
    "--tz",
    "Europe/Rome",
]


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RunStore:
    cfg = UpdateApplyConfig(enabled=True, state_dir=tmp_path / "apply")
    s = RunStore(cfg.state_dir)
    monkeypatch.setattr(_update_policy, "get_update_apply_config", lambda: cfg)
    monkeypatch.setattr(plugin_updater, "_store", lambda: s)
    monkeypatch.setattr(_update_policy, "operator", lambda: "cli:ops")
    monkeypatch.setattr(plugin_updater, "_operator", lambda: "cli:ops")
    return s


def _root() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--format", choices=["text", "json"], default="text")
    register_parser(root.add_subparsers(dest="command"), argparse.HelpFormatter)
    cli_main._inject_global_format(root)
    return root


def _run(argv: list[str]) -> int:
    return handlers.cmd_update(_root().parse_args(argv))


def test_get_without_a_policy_says_off(store: RunStore, capsys) -> None:
    assert _run(["update", "policy", "get"]) == 0
    out = capsys.readouterr().out
    assert "policy: off" in out and "proposal: none" in out


def test_set_get_round_trip(store: RunStore, capsys) -> None:
    assert _run(SET_ON) == 0
    changed = capsys.readouterr().out
    assert "core: off -> patch" in changed and "plugins: off -> patch" in changed
    policy = store.get_policy()
    assert policy.core == "patch" and policy.plugins == "patch"
    assert policy.plugin_allowlist == {"aura", "wikigen"}
    assert policy.window is not None and policy.window.days == {5, 6}
    assert (policy.window.start.isoformat(), policy.window.end.isoformat()) == (
        "01:30:00",
        "05:00:00",
    )
    assert _run(["update", "policy", "get", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["policy"]["core"] == "patch"
    assert data["policy"]["window"]["days"] == [5, 6]
    assert data["proposal"] is None
    assert _run(["update", "policy", "set", "--core", "off", "--plugins", "off"]) == 0
    assert store.get_policy() == POLICY_OFF


def test_get_shows_a_pending_proposal(store: RunStore, capsys) -> None:
    store.propose_policy(POLICY_OFF, actor="alice", ttl_seconds=600)
    assert _run(["update", "policy", "get"]) == 0
    assert "proposal: by alice" in capsys.readouterr().out


def test_set_rejects_unknown_timezone(store: RunStore, capsys) -> None:
    argv = [*SET_ON[:-1], "Mars/Olympus"]
    assert _run(argv) == 2
    assert "unknown timezone" in capsys.readouterr().err
    assert store.get_policy() == POLICY_OFF


def test_set_requires_window_when_enabling_a_channel(store: RunStore, capsys) -> None:
    assert _run(["update", "policy", "set", "--plugins", "patch"]) == 2
    assert "maintenance window" in capsys.readouterr().err
    assert store.get_policy() == POLICY_OFF


@pytest.mark.parametrize(
    ("flag", "value"), [("--days", "mon,funday"), ("--start", "25:00")]
)
def test_set_rejects_bad_window_input(
    store: RunStore, capsys, flag: str, value: str
) -> None:
    argv = list(SET_ON)
    argv[argv.index(flag) + 1] = value
    assert _run(argv) == 2
    assert store.get_policy() == POLICY_OFF


def test_set_io_error_is_exit_1(
    store: RunStore, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_: object, **__: object) -> None:
        raise PermissionError("read-only")

    monkeypatch.setattr(RunStore, "set_policy", broken)
    assert _run(SET_ON) == 1


def test_policy_set_is_audited_with_cli_actor(
    store: RunStore, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        assert _run(SET_ON) == 0
    line = (
        (store.root / "policy.log.jsonl").read_text(encoding="utf-8").splitlines()[-1]
    )
    assert json.loads(line)["actor"] == "cli:ops"
    assert any(
        "AUDIT | UPDATE_POLICY | set" in r.getMessage()
        and "by=cli:ops" in r.getMessage()
        for r in caplog.records
    )


def _make(store: RunStore, plugin: str, *, subject: str | None = None) -> str:
    return store.create(
        kind=RunKind.UPDATE,
        plugin=plugin,
        subject=subject,
        from_version="1.0.0",
        to_version="1.0.1",
        tarball_sha256="a" * 64,
        requested_by="policy:auto-patch",
        approval_required=False,
        approval_ttl_seconds=60,
    ).id


def test_runs_lists_core_and_plugin_runs(store: RunStore, capsys) -> None:
    plugin_run = _make(store, "aura")
    core_run = _make(store, "baselith_core", subject="core")
    assert _run(["update", "runs"]) == 0
    out = capsys.readouterr().out
    assert plugin_run in out and core_run in out
    assert "plugin:aura" in out and " core " in out and "policy:auto-patch" in out
    assert _run(["update", "runs", "--subject", "core", "--format", "json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == [core_run]
    assert rows[0]["subject"] == "core" and rows[0]["to_version"] == "1.0.1"
    assert _run(["update", "runs", "--subject", "aura", "--limit", "1"]) == 0
    assert plugin_run in capsys.readouterr().out


def test_resolve_core_rollback_failed(store: RunStore, capsys) -> None:
    core_run = _make(store, "baselith_core", subject="core")
    store.transition(core_run, RunState.ROLLBACK_FAILED)
    assert _run(["update", "resolve", core_run]) == 0
    assert store.get(core_run).state is RunState.FAILED  # type: ignore[union-attr]
    assert store.active("baselith_core") is None
    assert _run(["update", "resolve", core_run]) == 1  # no longer rollback_failed


def _boot(store: RunStore, version: str) -> None:
    from datetime import UTC, datetime

    from baselith.core.plugin_updates.apply.boot_report import BootReport

    report = BootReport(
        pid=1,
        booted_at=datetime.now(UTC),
        core_version="1.3.1",
        framework_version=version,
        plugins={},
    )
    store.boot_dir.mkdir(parents=True, exist_ok=True)
    (store.boot_dir / "1.json").write_text(report.model_dump_json())


def test_resolve_frees_a_stuck_finalizing_core_run(store: RunStore, capsys) -> None:
    core_run = _make(store, "baselith_core", subject="core")
    store.transition(core_run, RunState.FINALIZING)
    _boot(store, "1.0.0")  # the API does not serve the target
    assert _run(["update", "resolve", core_run]) == 0
    done = store.get(core_run)
    assert done is not None and done.state is RunState.FAILED
    assert "resolved by operator" in done.message
    assert store.active("baselith_core") is None
    actor = store.journal(core_run)[-1].actor
    assert actor is not None and actor.startswith("cli:")


def test_resolve_finalizing_succeeds_when_the_api_serves_the_target(
    store: RunStore,
) -> None:
    core_run = _make(store, "baselith_core", subject="core")
    store.transition(core_run, RunState.FINALIZING)
    _boot(store, "1.0.1")
    assert _run(["update", "resolve", core_run]) == 0
    done = store.get(core_run)
    assert done is not None and done.state is RunState.SUCCEEDED
    assert "resolved by operator" in done.message


def test_resolve_refuses_a_run_in_flight(store: RunStore) -> None:
    core_run = _make(store, "baselith_core", subject="core")
    store.transition(core_run, RunState.MIGRATING)
    assert _run(["update", "resolve", core_run]) == 1
    assert store.get(core_run).state is RunState.MIGRATING  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "argv",
    [
        ["--format", "json", "update", "policy", "get"],
        ["update", "--format", "json", "policy", "get"],
        ["update", "policy", "--format", "json", "get"],
        ["update", "policy", "get", "--format", "json"],
    ],
)
def test_nested_format_flag_in_every_position(
    store: RunStore, capsys, argv: list[str]
) -> None:
    assert _run(argv) == 0
    assert json.loads(capsys.readouterr().out)["policy"]["core"] == "off"


def test_policy_commands_never_load_plugin_code(tmp_path: Path) -> None:
    code = (
        "import sys, os\n"
        "os.environ['UPDATE_APPLY_STATE_DIR'] = sys.argv[1]\n"
        "from baselith.core.cli.commands.update import run_update\n"
        "assert run_update('policy', {'policy_command': 'get'}) == 0\n"
        "assert run_update('runs', {}) == 0\n"
        "bad = [m for m in sys.modules if m == 'baselith.plugins'"
        " or m.startswith('baselith.plugins.')]\n"
        "assert not bad, bad\n"
    )
    subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "apply")],
        check=True,
        timeout=120,
        cwd=Path(__file__).resolve().parents[4],
    )
