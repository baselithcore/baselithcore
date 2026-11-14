from __future__ import annotations

import pytest
from pydantic import ValidationError

from baselith.core.config.plugin_update_apply import UpdateApplyConfig


def test_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("UPDATE_APPLY_ENABLED", "UPDATE_APPLY_RESTART_COMMAND"):
        monkeypatch.delenv(key, raising=False)
    cfg = UpdateApplyConfig()
    assert (
        cfg.enabled is False
        and cfg.restart_command == []
        and not cfg.restart_configured
    )
    assert cfg.keep_versions == 2 and cfg.health_timeout_seconds == 180


def test_unapproved_request_expires_after_24_hours_by_default() -> None:
    assert UpdateApplyConfig().approval_ttl_seconds == 24 * 3600


def test_restart_command_is_a_json_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "UPDATE_APPLY_RESTART_COMMAND",
        '["sudo","-n","/usr/bin/systemctl","restart","baselithcore.service"]',
    )
    cfg = UpdateApplyConfig()
    assert cfg.restart_command[0] == "sudo" and cfg.restart_configured


@pytest.mark.parametrize("argv", [[""], ["ok", "bad\x00"]])
def test_bad_argv_refused(argv: list[str]) -> None:
    with pytest.raises(ValidationError):
        UpdateApplyConfig(restart_command=argv)


@pytest.mark.parametrize(
    "url", ["ftp://x/health", "http:///health", "file:///etc/passwd"]
)
def test_health_url_must_be_http(url: str) -> None:
    with pytest.raises(ValidationError):
        UpdateApplyConfig(health_url=url)


def test_keep_versions_at_least_two() -> None:
    with pytest.raises(ValidationError):
        UpdateApplyConfig(keep_versions=1)


def test_state_dir_is_resolved_to_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    assert UpdateApplyConfig().state_dir.is_absolute()


def test_relative_state_dir_refused_when_enabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert UpdateApplyConfig().state_dir.is_absolute()  # switch off: just resolved
    with pytest.raises(ValidationError):
        UpdateApplyConfig(enabled=True)
    assert UpdateApplyConfig(
        enabled=True, state_dir="/var/lib/x"
    ).state_dir.is_absolute()


def test_core_install_settings_default_off_and_empty() -> None:
    cfg = UpdateApplyConfig()
    assert cfg.core_enabled is False and not cfg.backup_configured
    assert cfg.backup_command == [] and cfg.ignored_paths == []
    assert cfg.updater_restart_command == [] and cfg.backup_timeout_seconds == 1800


def test_backup_and_updater_restart_commands_parse_like_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "UPDATE_APPLY_BACKUP_COMMAND", '["/usr/local/bin/backup","--all"]'
    )
    monkeypatch.setenv(
        "UPDATE_APPLY_UPDATER_RESTART_COMMAND", "sudo,-n,systemctl,restart,updater"
    )
    monkeypatch.setenv("UPDATE_APPLY_IGNORED_PATHS", "ui/dist/,configs/plugins.yaml")
    monkeypatch.setenv("UPDATE_APPLY_CORE_ENABLED", "true")
    cfg = UpdateApplyConfig()
    assert cfg.backup_command == ["/usr/local/bin/backup", "--all"]
    assert cfg.backup_configured and cfg.core_enabled
    assert cfg.updater_restart_command[-1] == "updater"
    assert cfg.ignored_paths == ["ui/dist", "configs/plugins.yaml"]


@pytest.mark.parametrize("argv", [[""], ["ok", "bad\x00"]])
def test_backup_command_rejects_empty_and_nul(argv: list[str]) -> None:
    with pytest.raises(ValidationError):
        UpdateApplyConfig(backup_command=argv)
    with pytest.raises(ValidationError):
        UpdateApplyConfig(updater_restart_command=argv)


@pytest.mark.parametrize(
    "path", ["/etc", "../up", "a/../b", "./a", "a//b", "", "/", "a\\b", "a\x00"]
)
def test_ignored_paths_reject_absolute_and_dotdot(path: str) -> None:
    with pytest.raises(ValidationError):
        UpdateApplyConfig(ignored_paths=[path])


@pytest.mark.parametrize("seconds", [9, 14401])
def test_backup_timeout_is_bounded(seconds: int) -> None:
    with pytest.raises(ValidationError):
        UpdateApplyConfig(backup_timeout_seconds=seconds)
