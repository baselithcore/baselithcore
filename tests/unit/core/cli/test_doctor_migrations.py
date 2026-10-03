"""``baselith doctor`` must fail when the migration scripts cannot be located.

The check used to report only the ``DB_MIGRATIONS_ON_STARTUP`` mode and PASS
unconditionally — including on a wheel install whose migrations were not
packaged, where every upgrade was bound to fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.cli.commands import doctor, doctor_checks, doctor_migrations
from core.db import migration_config
from core.db.migration_config import MigrationsNotFoundError


def test_passes_and_names_the_head_when_scripts_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no alembic.ini, no migrations/ here
    monkeypatch.setenv("DB_MIGRATIONS_ON_STARTUP", "true")

    result = doctor_migrations.check_migrations_mode()

    assert result.passed
    assert result.name == "DB Migrations"
    assert migration_config.migration_heads()[0] in result.details


def test_explicit_mode_still_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_MIGRATIONS_ON_STARTUP", "false")

    result = doctor_migrations.check_migrations_mode()

    assert result.passed
    assert result.message == "Startup migrations disabled"


def test_fails_when_scripts_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing() -> tuple[str, ...]:
        raise MigrationsNotFoundError("Alembic migrations not found at /nowhere")

    monkeypatch.setattr(migration_config, "migration_heads", _missing)

    result = doctor_migrations.check_migrations_mode()

    assert not result.passed
    assert result.severity == "fail"
    assert "/nowhere" in result.details


def test_fails_when_a_revision_cannot_be_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _broken() -> tuple[str, ...]:
        raise SyntaxError("bad revision module")

    monkeypatch.setattr(migration_config, "migration_heads", _broken)

    result = doctor_migrations.check_migrations_mode()

    assert not result.passed
    assert "bad revision module" in result.details


def test_doctor_entrypoints_use_the_honest_check() -> None:
    assert doctor.check_migrations_mode is doctor_migrations.check_migrations_mode
    names = [check.name for check in doctor_checks.run_checks()]
    assert names.count("DB Migrations") == 1
