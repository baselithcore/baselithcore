"""``baselith update``: status and check print the update report, read-only."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

import baselith.core.cli.__main__ as cli_main
from baselith.core.cli import handlers
from baselith.core.cli.commands import _update_report, update
from baselith.core.cli.commands.update import (
    register_parser,
    render_report,
    run_update,
)
from baselith.core.plugin_updates.models import (
    CheckReport,
    Refusal,
    ReleaseInfo,
    SignedAssets,
    SystemUpdate,
    UpdateCandidate,
)


@pytest.fixture(autouse=True)
def _checks_on(monkeypatch):
    """``check`` is on unless a test says otherwise."""
    monkeypatch.setattr(
        update, "get_plugin_update_config", lambda: MagicMock(enabled=True)
    )


def _report(
    *,
    core_latest: str | None = None,
    plugin: str | None = None,
    latest: str = "0.27.0",
    sha: str | None = None,
    error: str | None = None,
) -> CheckReport:
    system = SystemUpdate(repo="o/core", installed_version="1.2.0")
    if core_latest:
        system = system.model_copy(
            update={
                "available": True,
                "latest": ReleaseInfo(
                    plugin="core",
                    version=core_latest,
                    tag=f"v{core_latest}",
                    published_at=None,
                ),
            }
        )
    candidates = []
    if plugin:
        candidates.append(
            UpdateCandidate(
                plugin=plugin,
                installed_version="0.26.5",
                latest=ReleaseInfo(
                    plugin=plugin, version=latest, tag=f"v{latest}", published_at=None
                ),
                available=True,
                signed_assets=(
                    SignedAssets(verified=True, tarball_sha256=sha) if sha else None
                ),
            )
        )
    return CheckReport(
        checked_at=datetime(2026, 11, 13, tzinfo=UTC),
        candidates=candidates,
        error=error,
        system=system,
    )


def test_render_lists_core_and_plugins():
    text = "\n".join(
        render_report(
            _report(core_latest="1.3.0", plugin="aura", latest="0.27.0", sha="a" * 64)
        )
    )
    assert "core 1.2.0 -> 1.3.0" in text
    assert "aura 0.26.5 -> 0.27.0" in text
    assert "a" * 64 in text


def test_render_notice_only_has_no_sha():
    text = "\n".join(render_report(_report(plugin="aura", sha=None)))
    assert "notice only" in text
    assert "sha256=" not in text


def test_render_up_to_date_core():
    assert "up to date" in "\n".join(render_report(_report()))


def test_status_without_report(monkeypatch, capsys):
    monkeypatch.setattr(update, "_load_cached", lambda: None)
    assert run_update("status", {"format": "text"}) == 0
    assert "no update report yet" in capsys.readouterr().out


def test_status_json_is_the_report(monkeypatch, capsys):
    report = _report(plugin="aura", sha="a" * 64)
    monkeypatch.setattr(update, "_load_cached", lambda: report)
    assert run_update("status", {"format": "json"}) == 0
    assert json.loads(capsys.readouterr().out)["candidates"][0]["plugin"] == "aura"


def test_check_reports_error_with_exit_1(monkeypatch, capsys):
    monkeypatch.setattr(
        update, "_check_now", AsyncMock(return_value=_report(error="offline"))
    )
    assert run_update("check", {"format": "text"}) == 1
    assert "offline" in capsys.readouterr().out


def test_check_ok_exits_0(monkeypatch, capsys):
    monkeypatch.setattr(
        update, "_check_now", AsyncMock(return_value=_report(plugin="aura"))
    )
    assert run_update("check", {"format": "text"}) == 0
    assert "aura" in capsys.readouterr().out


def test_unknown_subcommand_is_usage_error():
    assert run_update("bogus", {}) == 2


def test_parser_and_handler_dispatch(monkeypatch, capsys):
    monkeypatch.setattr(update, "_load_cached", lambda: None)
    root = _root()
    args = root.parse_args(["update", "status", "--format", "json"])
    assert args.update_command == "status" and args.format == "json"
    assert handlers.cmd_update(root.parse_args(["update"])) == 0
    assert "no update report yet" in capsys.readouterr().out


def _root() -> argparse.ArgumentParser:
    """A root parser built like the real one: global --format, then injection."""
    root = argparse.ArgumentParser()
    root.add_argument("--format", choices=["text", "json"], default="text")
    register_parser(root.add_subparsers(dest="command"), argparse.HelpFormatter)
    cli_main._inject_global_format(root)
    return root


@pytest.mark.parametrize(
    "argv",
    [
        ["--format", "json", "update", "status"],
        ["update", "--format", "json", "status"],
        ["update", "status", "--format", "json"],
    ],
)
def test_format_json_in_any_position(monkeypatch, capsys, argv):
    report = _report(plugin="aura", sha="a" * 64)
    monkeypatch.setattr(update, "_load_cached", lambda: report)
    args = _root().parse_args(argv)
    assert handlers.cmd_update(args) == 0
    assert json.loads(capsys.readouterr().out)["candidates"][0]["plugin"] == "aura"


def test_format_defaults_to_text(monkeypatch, capsys):
    monkeypatch.setattr(update, "_load_cached", lambda: _report())
    assert handlers.cmd_update(_root().parse_args(["update", "status"])) == 0
    assert capsys.readouterr().out.startswith("checked at")


def _security(report: CheckReport, **fields) -> CheckReport:
    system = report.system.model_copy(update={"security": True, "severity": "high"})
    return report.model_copy(update={"system": system.model_copy(update=fields)})


def test_security_without_newer_release_is_shown():
    text = "\n".join(render_report(_security(_report())))
    assert "SECURITY (high)" in text
    assert "core 1.2.0" in text


def test_security_with_failed_release_lookup_is_shown():
    report = _security(_report(), error="timeout")
    text = "\n".join(render_report(report))
    assert "check failed (timeout)" in text
    assert "SECURITY (high)" in text


def test_security_with_plugin_updates_is_shown():
    report = _security(_report(core_latest="1.3.0", plugin="aura"))
    text = "\n".join(render_report(report))
    assert "core 1.2.0 -> 1.3.0  SECURITY (high)" in text
    assert "aura 0.26.5 -> 0.27.0" in text


def test_plugin_only_updates_have_no_security_mark():
    text = "\n".join(render_report(_report(plugin="aura")))
    assert "SECURITY" not in text


def test_carried_over_core_error_is_printed():
    report = _report(core_latest="1.3.0")
    report = report.model_copy(
        update={"system": report.system.model_copy(update={"error": "timeout"})}
    )
    text = "\n".join(render_report(report))
    assert "last check failed: timeout; showing last known state" in text
    assert "core 1.2.0 -> 1.3.0" in text


def test_advisories_unavailable_is_printed_but_does_not_fail(monkeypatch, capsys):
    report = _report(core_latest="1.3.0")
    err = "advisories unavailable (HTTP 403)"
    report = report.model_copy(
        update={"system": report.system.model_copy(update={"error": err})}
    )
    monkeypatch.setattr(update, "_check_now", AsyncMock(return_value=report))
    assert run_update("check", {"format": "text"}) == 0
    assert err in capsys.readouterr().out


def _with_system_error(error: str, **fields) -> CheckReport:
    report = _report(core_latest="1.3.0")
    system = report.system.model_copy(update={"error": error, **fields})
    return report.model_copy(update={"system": system})


@pytest.mark.parametrize(
    "report",
    [
        _with_system_error("timeout"),
        _with_system_error("timeout; advisories unavailable (HTTP 403)"),
        _report(error="offline"),
        _report(plugin="aura").model_copy(
            update={
                "candidates": [
                    UpdateCandidate(
                        plugin="aura",
                        installed_version="1",
                        latest=None,
                        available=False,
                        refusal=Refusal.SOURCE_ERROR,
                        detail="boom",
                    )
                ]
            }
        ),
    ],
)
def test_check_exit_1_on_network_failures(monkeypatch, capsys, report):
    monkeypatch.setattr(update, "_check_now", AsyncMock(return_value=report))
    assert run_update("check", {"format": "text"}) == 1


def test_check_exception_prints_and_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(
        update, "_check_now", AsyncMock(side_effect=RuntimeError("kaput"))
    )
    assert run_update("check", {"format": "text"}) == 1
    assert "RuntimeError" in capsys.readouterr().out


def test_check_off_prints_how_to_turn_on_and_writes_nothing(monkeypatch, capsys):
    monkeypatch.setattr(
        update, "get_plugin_update_config", lambda: MagicMock(enabled=False)
    )
    check = AsyncMock()
    monkeypatch.setattr(update, "_check_now", check)
    assert run_update("check", {"format": "text"}) == 0
    out = capsys.readouterr().out
    assert "CORE_UPDATE_REPO" in out and "PLUGIN_UPDATE_SOURCES_FILE" in out
    check.assert_not_called()


def test_status_over_a_real_corrupt_cache(monkeypatch, tmp_path, capsys):
    (tmp_path / "last_check.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("PLUGIN_UPDATE_CACHE_DIR", str(tmp_path))
    from baselith.core.config.plugin_updates import get_plugin_update_config

    get_plugin_update_config.cache_clear()
    try:
        assert update._load_cached() is None
        assert run_update("status", {"format": "text"}) == 0
    finally:
        get_plugin_update_config.cache_clear()
    assert "no update report yet" in capsys.readouterr().out


def test_load_served_report_tolerates_every_failure(monkeypatch):
    def boom():
        raise OSError("denied")

    monkeypatch.setattr(_update_report, "get_plugin_update_config", boom)
    assert _update_report.load_served_report() is None


def test_load_cached_uses_the_served_report(monkeypatch):
    sentinel = _report()
    monkeypatch.setattr(update, "load_served_report", lambda: sentinel)
    assert update._load_cached() is sentinel


def test_served_report_drops_a_notice_about_another_version(monkeypatch, tmp_path):
    """The shared helper applies the server's filters, not the raw cache."""
    from baselith.core.config.plugin_updates import get_plugin_update_config
    from baselith.core.plugin_updates.cache import UpdateCache

    monkeypatch.setenv("PLUGIN_UPDATE_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("CORE_UPDATE_REPO", "o/core")
    get_plugin_update_config.cache_clear()
    try:
        old = _security(_report(core_latest="1.3.0"))  # installed 1.2.0, not ours
        UpdateCache(tmp_path).save(old)
        served = _update_report.load_served_report()
    finally:
        get_plugin_update_config.cache_clear()
    assert served is not None and served.system is None


def test_status_and_check_print_the_same_lines(monkeypatch, capsys):
    report = _report(core_latest="1.3.0", plugin="aura", sha="a" * 64)
    monkeypatch.setattr(update, "_load_cached", lambda: report)
    monkeypatch.setattr(update, "_check_now", AsyncMock(return_value=report))
    run_update("status", {"format": "text"})
    status = capsys.readouterr().out
    run_update("check", {"format": "text"})
    assert capsys.readouterr().out == status


def test_status_json_without_report_is_valid_json(monkeypatch, capsys):
    monkeypatch.setattr(update, "_load_cached", lambda: None)
    assert run_update("status", {"format": "json"}) == 0
    assert json.loads(capsys.readouterr().out) is None


class TestPluginCliScan:
    @pytest.mark.parametrize(
        ("argv", "skips"),
        [
            (["update", "status"], True),
            (["plugin-updater", "status"], True),
            (["--format", "json", "update"], True),
            (["--format=json", "update", "status"], True),
            (["info"], False),
            (["info", "update"], False),
            (["--format", "update", "info"], False),
            ([], False),
        ],
    )
    def test_skips_only_for_the_first_positional(self, argv, skips):
        assert cli_main._skips_plugin_clis(argv) is skips

    @pytest.mark.parametrize(
        ("command", "called"),
        [("update", False), ("plugin-updater", False), ("info", True)],
    )
    def test_main_scan(self, monkeypatch, tmp_path, command, called):
        monkeypatch.chdir(tmp_path)
        scan = MagicMock()
        monkeypatch.setattr(cli_main, "_register_plugin_clis", scan)
        monkeypatch.setattr(cli_main, "ensure_checkout_precedence", lambda: None)
        monkeypatch.setattr(cli_main, "_configure_cli_logging", lambda: None)
        monkeypatch.setattr(sys, "argv", ["baselith", command, "--version"])
        with pytest.raises(SystemExit):
            cli_main.main()
        assert scan.called is called


async def test_release_error_parses_real_check_system_output():
    """The discriminator reads what ``check_system`` really writes, not a guess."""
    from baselith.core.plugin_updates.system import check_system
    from tests.unit.core.plugin_updates.test_system import _rel, _source

    releases = [_rel("v1.2.0"), _rel("v1.3.0")]
    only_advisories = await check_system(
        "1.2.0", "o/core", source=_source(releases, adv_status=500)
    )
    assert only_advisories.error is not None
    assert only_advisories.error.endswith(")")
    assert _update_report.release_error(only_advisories) is None

    both = await check_system(
        "1.2.0", "o/core", source=_source(rel_status=500, adv_status=500)
    )
    assert _update_report.release_error(both) is not None


def test_check_off_leaves_the_cache_directory_empty(monkeypatch, tmp_path):
    """A check that is off writes no cache file, not even an empty report."""
    from baselith.core.config.plugin_updates import PluginUpdateConfig

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    config = PluginUpdateConfig(
        core_update_repo="", sources_file=None, cache_dir=cache_dir
    )
    assert not config.enabled
    monkeypatch.setattr(update, "get_plugin_update_config", lambda: config)
    assert update.run_update("check", {}) == 0
    assert list(cache_dir.iterdir()) == []


def _with_distribution(report: CheckReport, **fields) -> CheckReport:
    notice = SystemUpdate(
        component="distribution", repo="o/dist", installed_version="1.14.0"
    )
    return report.model_copy(update={"distribution": notice.model_copy(update=fields)})


def test_render_shows_the_distribution_before_the_core():
    latest = ReleaseInfo(
        plugin="core", version="1.15.0", tag="v1.15.0", published_at=None
    )
    report = _with_distribution(_report(), available=True, latest=latest)
    lines = render_report(report)
    assert "distribution 1.14.0 -> 1.15.0" in lines
    assert lines.index("distribution 1.14.0 -> 1.15.0") < lines.index(
        "core 1.2.0: up to date"
    )


def test_render_without_distribution_has_no_distribution_line():
    assert not any("distribution" in line for line in render_report(_report()))


def test_failed_distribution_lookup_fails_the_check(monkeypatch):
    report = _with_distribution(_report(), error="timeout")
    monkeypatch.setattr(update, "_check_now", AsyncMock(return_value=report))
    assert run_update("check", {"format": "text"}) == 1
