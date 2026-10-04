"""``baselith db migrate`` and the startup upgrade run the PACKAGED migrations.

Both used to need ``alembic.ini`` in the working directory: the CLI refused to
start without it and ``ensure_schema`` built its config from ``os.getcwd()``,
so neither worked from a wheel install.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.cli.commands import db as db_cmd
from core.cli.commands.doctor_checks import CheckResult
from core.db import migrate, migration_config, schema
from core.db.migration_config import MigrationsNotFoundError


@pytest.fixture
def postgres_up(monkeypatch: pytest.MonkeyPatch) -> None:
    import core.cli.commands.doctor as doctor

    monkeypatch.setattr(
        doctor, "check_postgres", lambda: CheckResult("PostgreSQL", True, "ok")
    )


def test_cli_runs_the_packaged_upgrade_outside_the_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    postgres_up: None,
) -> None:
    monkeypatch.chdir(tmp_path)  # no alembic.ini here
    seen: dict[str, Any] = {}

    def _run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(db_cmd.subprocess, "run", _run)

    assert db_cmd.cmd_migrate(json_output=True) == 0
    assert seen["cmd"] == [sys.executable, "-m", "core.db.migrate"]
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_cli_reports_missing_scripts_without_touching_the_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _missing() -> Path:
        raise MigrationsNotFoundError("Alembic migrations not found at /nowhere")

    monkeypatch.setattr(migration_config, "migrations_dir", _missing)
    run = MagicMock()
    monkeypatch.setattr(db_cmd.subprocess, "run", run)

    assert db_cmd.cmd_migrate(json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert "/nowhere" in payload["message"]
    run.assert_not_called()


def test_upgrade_head_uses_the_packaged_config_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command

    monkeypatch.chdir(tmp_path)
    events: list[str] = []

    class _Lock:
        def __enter__(self) -> None:
            events.append("lock")

        def __exit__(self, *exc: object) -> None:
            events.append("unlock")

    def _upgrade(config: Any, revision: str) -> None:
        events.append(f"upgrade:{config.get_main_option('script_location')}")
        assert revision == "head"

    monkeypatch.setattr(schema, "_migration_leader_lock", _Lock)
    monkeypatch.setattr(command, "upgrade", _upgrade)

    schema.upgrade_head()

    assert events == [
        "lock",
        f"upgrade:{migration_config.migrations_dir()}",
        "unlock",
    ]


async def test_ensure_schema_delegates_to_upgrade_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upgrade = MagicMock()
    monkeypatch.setattr(schema, "upgrade_head", upgrade)

    await schema.ensure_schema()

    upgrade.assert_called_once_with()


def test_migrate_module_exits_nonzero_when_scripts_are_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _missing() -> None:
        raise MigrationsNotFoundError("Alembic migrations not found at /nowhere")

    monkeypatch.setattr(schema, "upgrade_head", _missing)

    assert migrate.main() == 1
    assert "/nowhere" in capsys.readouterr().err
