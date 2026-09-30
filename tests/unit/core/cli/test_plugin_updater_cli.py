"""``baselith plugin-updater``: request, rollback, resolve, status, serve refusals."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pytest

import core.cli.__main__ as cli_main
from core.cli import handlers
from core.cli.commands import plugin_updater as cli
from core.cli.commands.plugin_updater import register_parser, run_plugin_updater
from core.plugin_updates.apply.models import RunKind, RunState
from core.plugin_updates.apply.store import RunStore

SHA = "f" * 64


@pytest.fixture()
def state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("UPDATE_APPLY_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("BASELITH_PLUGIN_OVERLAY_DIR", raising=False)
    from core.config.plugin_update_apply import get_update_apply_config

    get_update_apply_config.cache_clear()
    yield tmp_path
    get_update_apply_config.cache_clear()


def test_request_creates_an_approved_run(state: Path) -> None:
    rc = run_plugin_updater(
        "request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA}
    )
    assert rc == 0
    run = RunStore(state).active("demo")
    assert run is not None and run.state is RunState.APPROVED
    assert run.requested_by.startswith("cli:") and run.tarball_sha256 == SHA


def test_request_refuses_a_bad_pin_or_name(state: Path) -> None:
    assert (
        run_plugin_updater("request", {"plugin": "demo", "version": "1", "sha256": "x"})
        == 2
    )
    assert (
        run_plugin_updater("request", {"plugin": "../x", "version": "1", "sha256": SHA})
        == 2
    )
    assert RunStore(state).unfinished() == []


def test_second_request_conflicts(state: Path) -> None:
    args = {"plugin": "demo", "version": "1.2.0", "sha256": SHA}
    assert run_plugin_updater("request", args) == 0
    assert run_plugin_updater("request", args) == 1


def test_resolve_releases_a_failed_rollback(state: Path) -> None:
    store = RunStore(state)
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    run = store.active("demo")
    store.transition(run.id, RunState.ROLLBACK_FAILED)
    assert run_plugin_updater("resolve", {"run_id": run.id}) == 0
    assert store.active("demo") is None
    done = store.get(run.id)
    assert done.state is RunState.FAILED and done.message == "resolved by operator"


def test_resolve_refuses_any_other_state(state: Path) -> None:
    store = RunStore(state)
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    run = store.active("demo")
    assert run_plugin_updater("resolve", {"run_id": run.id}) == 1
    assert run_plugin_updater("resolve", {"run_id": "nope"}) == 1
    assert store.get(run.id).state is RunState.APPROVED


def test_rollback_targets_the_last_successful_update_without_approval(
    state: Path,
) -> None:
    store = RunStore(state)
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    first = store.active("demo")
    store.transition(
        first.id, RunState.SUCCEEDED, previous_target="demo-1.1.0", target="demo-1.2.0"
    )
    assert run_plugin_updater("rollback", {"plugin": "demo"}) == 0
    run = store.active("demo")
    assert run is not None and run.kind is RunKind.ROLLBACK
    assert run.state is RunState.APPROVED  # D1: no second approval
    assert run.target == "demo-1.1.0" and run.requested_by.startswith("cli:")


def test_rollback_without_history_fails(state: Path) -> None:
    assert run_plugin_updater("rollback", {"plugin": "demo"}) == 1
    assert RunStore(state).unfinished() == []


def test_status_prints_json(state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    capsys.readouterr()
    assert run_plugin_updater("status", {"format": "json"}) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["heartbeat"] is None and out["unfinished"][0]["plugin"] == "demo"


def test_serve_without_overlay_exits_2(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_plugin_updater("serve", {}) == 2
    assert "BASELITH_PLUGIN_OVERLAY_DIR" in capsys.readouterr().err


def test_serve_crash_exits_1_without_a_traceback(
    state: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "pw-owner-secret"

    def boom(config: object) -> object:
        creds = {"POSTGRES_PASSWORD": secret}  # a local a reporter could capture
        raise ValueError(f"cannot use {creds}")  # printing str(exc) would leak it

    monkeypatch.setattr(cli, "build_executor", boom)
    assert run_plugin_updater("serve", {}) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    assert "Traceback" not in captured.err


def test_parser_and_dispatch() -> None:
    parser = argparse.ArgumentParser()
    register_parser(parser.add_subparsers(dest="command"), argparse.HelpFormatter)
    args = parser.parse_args(
        ["plugin-updater", "request", "demo", "--version", "1", "--sha256", SHA]
    )
    assert args.plugin_updater_command == "request" and args.plugin == "demo"
    args = parser.parse_args(["plugin-updater", "serve"])
    assert not handlers.is_scoped_command("plugin-updater", args)
    args = parser.parse_args(["plugin-updater", "status"])
    assert handlers.is_scoped_command("plugin-updater", args)
    assert "plugin-updater" in cli_main.COMMANDS_MAP["INFRASTRUCTURE"]
    assert "plugin-updater" in cli_main.COMMAND_HANDLERS_MAP


def _actor() -> str:
    import os
    import pwd

    return f"cli:{pwd.getpwuid(os.getuid()).pw_name}"


def test_operator_actions_are_audited_with_the_actor(
    state: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStore(state)
    with caplog.at_level(logging.INFO):
        run_plugin_updater(
            "request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA}
        )
        run = store.active("demo")
        store.transition(run.id, RunState.ROLLBACK_FAILED)
        run_plugin_updater("resolve", {"run_id": run.id})
    lines = [
        r.getMessage()
        for r in caplog.records
        if "AUDIT | PLUGIN_UPDATE" in r.getMessage()
    ]
    assert (
        f"AUDIT | PLUGIN_UPDATE | request run={run.id} plugin=demo by={_actor()}"
        in lines
    )
    assert (
        f"AUDIT | PLUGIN_UPDATE | resolve run={run.id} plugin=demo by={_actor()}"
        in lines
    )
    assert run.requested_by == _actor()


def test_rollback_finds_the_update_behind_many_failed_runs(
    state: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = RunStore(state)
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    first = store.active("demo")
    store.transition(
        first.id, RunState.SUCCEEDED, previous_target="demo-1.1.0", target="demo-1.2.0"
    )
    for _ in range(25):
        run_plugin_updater(
            "request", {"plugin": "demo", "version": "1.3.0", "sha256": SHA}
        )
        store.transition(store.active("demo").id, RunState.FAILED)
    with caplog.at_level(logging.INFO):
        assert run_plugin_updater("rollback", {"plugin": "demo"}) == 0
    run = store.active("demo")
    assert run is not None and run.target == "demo-1.1.0"
    assert any(
        f"AUDIT | PLUGIN_UPDATE | rollback run={run.id} plugin=demo by="
        in r.getMessage()
        for r in caplog.records
    )


def test_rollback_to_where_the_plugin_already_is_is_a_noop(
    state: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore(state)
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    first = store.active("demo")
    # previous_target None = bundled; with no overlay the plugin runs bundled already
    store.transition(
        first.id, RunState.SUCCEEDED, previous_target=None, target="demo-1.2.0"
    )
    capsys.readouterr()
    assert run_plugin_updater("rollback", {"plugin": "demo"}) == 0
    assert store.active("demo") is None
    assert "already" in capsys.readouterr().out


def test_status_text_format(state: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_plugin_updater("request", {"plugin": "demo", "version": "1.2.0", "sha256": SHA})
    capsys.readouterr()
    assert run_plugin_updater("status", {"format": "text"}) == 0
    out = capsys.readouterr().out
    assert "heartbeat: none" in out and "demo" in out and "approved" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
