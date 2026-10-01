from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config.plugin_update_apply import UpdateApplyConfig


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
