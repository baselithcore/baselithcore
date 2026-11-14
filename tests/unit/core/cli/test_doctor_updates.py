"""``baselith doctor`` Updates check: reads the cached report, never fails the run."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from baselith.core.cli.commands import doctor, doctor_updates
from baselith.core.cli.commands.doctor_updates import check_updates
from baselith.core.plugin_updates.models import (
    CheckReport,
    ReleaseInfo,
    SystemUpdate,
    UpdateCandidate,
)


def _report(
    *, latest: str | None = None, security: bool = False, plugins: int = 0
) -> CheckReport:
    system = SystemUpdate(repo="o/core", installed_version="1.2.0")
    if latest:
        system = system.model_copy(
            update={
                "available": True,
                "security": security,
                "latest": ReleaseInfo(
                    plugin="core", version=latest, tag=f"v{latest}", published_at=None
                ),
            }
        )
    candidates = [
        UpdateCandidate(
            plugin=f"p{i}",
            installed_version="1",
            latest=ReleaseInfo(
                plugin=f"p{i}", version="2", tag="v2", published_at=None
            ),
            available=True,
        )
        for i in range(plugins)
    ]
    return CheckReport(
        checked_at=datetime(2026, 11, 13, tzinfo=UTC),
        candidates=candidates,
        system=system,
    )


@pytest.fixture(autouse=True)
def _checks_on(monkeypatch):
    monkeypatch.setattr(
        doctor_updates, "get_plugin_update_config", lambda: MagicMock(enabled=True)
    )


def _patch(monkeypatch: pytest.MonkeyPatch, value: object) -> None:
    def load() -> CheckReport | None:
        if isinstance(value, Exception):
            raise value
        return value  # type: ignore[return-value]

    monkeypatch.setattr(doctor_updates, "_load_cached", load)


def test_no_report_is_a_pass_naming_the_check_command(monkeypatch):
    _patch(monkeypatch, None)
    result = check_updates()
    assert result.name == "Updates"
    assert result.passed
    assert result.message == "No update report yet"
    assert "baselith update check" in result.details


def test_security_update_is_a_non_blocking_warning(monkeypatch):
    _patch(monkeypatch, _report(latest="1.3.0", security=True))
    result = check_updates()
    assert not result.passed
    assert result.severity == "warn"
    assert result.message == "Security advisory affects core 1.2.0; fixed in 1.3.0"
    assert doctor._severity_for(result) == "warn"


def test_update_available_passes_with_plugin_count(monkeypatch):
    _patch(monkeypatch, _report(latest="1.3.0", plugins=2))
    result = check_updates()
    assert result.passed
    assert result.message.startswith("Update available: core 1.3.0")
    assert "2 plugin update(s)" in result.message


def test_up_to_date(monkeypatch):
    _patch(monkeypatch, _report())
    result = check_updates()
    assert result.passed
    assert result.message == "Up to date (checked 2026-11-13)"


def test_unreadable_report_is_a_pass_with_the_exception_type(monkeypatch):
    _patch(monkeypatch, PermissionError("denied"))
    result = check_updates()
    assert result.passed
    assert result.message == "Update report unreadable"
    assert "PermissionError" in result.details


def test_security_warning_does_not_fail_the_doctor_run(monkeypatch):
    monkeypatch.setattr(
        doctor,
        "run_checks",
        lambda include_plugins=True: [
            doctor_updates.CheckResult("Updates", False, "x", "", "warn")
        ],
    )
    assert doctor.run_doctor(json_output=True) == 0


def test_updates_check_is_last_core_check(monkeypatch):
    """Updates runs directly after DB Migrations, read from the real order."""
    names = [
        name
        for name in dir(doctor)
        if name.startswith("check_") and callable(getattr(doctor, name))
    ]
    executed: list[str] = []
    for name in names:
        label = {"check_migrations_mode": "DB Migrations", "check_updates": "Updates"}
        result_name = label.get(name, name)

        def stub(_name: str = result_name) -> doctor_updates.CheckResult:
            executed.append(_name)
            return doctor_updates.CheckResult(_name, True, "")

        monkeypatch.setattr(doctor, name, stub)
    order = [r.name for r in doctor.run_checks(include_plugins=False)]
    assert order == executed  # every result came from a stub: nothing real ran
    assert order[-1] == "Updates"
    assert order[order.index("DB Migrations") + 1] == "Updates"


def _with_system(report: CheckReport, **fields) -> CheckReport:
    return report.model_copy(update={"system": report.system.model_copy(update=fields)})


def test_security_without_a_newer_release_is_still_a_warning(monkeypatch):
    _patch(monkeypatch, _with_system(_report(), security=True, severity="high"))
    result = check_updates()
    assert not result.passed and result.severity == "warn"
    assert result.message == "Security advisory affects core 1.2.0"


def test_security_with_a_fixed_release_names_it(monkeypatch):
    _patch(monkeypatch, _report(latest="1.3.0", security=True))
    result = check_updates()
    assert not result.passed and result.severity == "warn"
    assert result.message == "Security advisory affects core 1.2.0; fixed in 1.3.0"


def test_security_with_failed_release_lookup_is_a_warning(monkeypatch):
    _patch(
        monkeypatch,
        _with_system(_report(), security=True, error="timeout"),
    )
    result = check_updates()
    assert not result.passed and result.severity == "warn"
    assert result.message == "Security advisory affects core 1.2.0"


def test_security_together_with_plugin_updates_stays_a_warning(monkeypatch):
    _patch(monkeypatch, _report(latest="1.3.0", security=True, plugins=2))
    result = check_updates()
    assert not result.passed and result.severity == "warn"


def test_failed_check_is_not_up_to_date(monkeypatch):
    _patch(monkeypatch, _report().model_copy(update={"error": "offline"}))
    result = check_updates()
    assert result.passed
    assert result.message == "Last check failed (offline)"


def test_failed_core_lookup_is_not_up_to_date(monkeypatch):
    _patch(monkeypatch, _with_system(_report(), error="timeout"))
    result = check_updates()
    assert result.passed
    assert result.message == "Last check failed (timeout)"


def test_unavailable_advisories_alone_is_up_to_date(monkeypatch):
    _patch(
        monkeypatch,
        _with_system(_report(), error="advisories unavailable (HTTP 403)"),
    )
    assert check_updates().message.startswith("Up to date")


def test_stale_core_notice_is_not_up_to_date(monkeypatch):
    """The served report lost its core notice (another version): ask for a check."""
    _patch(monkeypatch, _report().model_copy(update={"system": None}))
    result = check_updates()
    assert result.passed and result.message == "No update report yet"
    assert "baselith update check" in result.details


def test_plugin_only_checks_with_no_plugins_are_up_to_date(monkeypatch):
    monkeypatch.setattr(
        doctor_updates,
        "get_plugin_update_config",
        lambda: MagicMock(enabled=True, system_checks_enabled=False),
    )
    _patch(monkeypatch, _report().model_copy(update={"system": None}))
    assert check_updates().message.startswith("Up to date")


def test_config_off_is_checks_off(monkeypatch):
    monkeypatch.setattr(
        doctor_updates, "get_plugin_update_config", lambda: MagicMock(enabled=False)
    )
    _patch(monkeypatch, _report())
    result = check_updates()
    assert result.passed and result.message == "Update checks off"
    assert "CORE_UPDATE_REPO" in result.details


def test_unreadable_is_logged_at_debug(monkeypatch, caplog):
    _patch(monkeypatch, PermissionError("denied"))
    with caplog.at_level(logging.DEBUG, logger=doctor_updates.__name__):
        check_updates()
    assert any(r.exc_info for r in caplog.records)
