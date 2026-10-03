"""The production migration check compares against the PACKAGED head.

It built its Alembic config from ``alembic.ini`` in the cwd, so a wheel
install — no ini, no scripts beside the process — always logged "Could not
verify migration status" and never compared anything.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy
from alembic.runtime import migration

from core.api import startup_checks
from core.db.migration_config import migration_heads


class _Ctx:
    def __init__(self, revision: str) -> None:
        self._revision = revision

    def get_current_revision(self) -> str:
        return self._revision


class _Engine:
    def connect(self) -> Any:
        import contextlib

        return contextlib.nullcontext(object())

    def dispose(self) -> None:
        return None


@pytest.fixture
def production_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)  # no alembic.ini here
    monkeypatch.setattr(startup_checks, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(startup_checks, "CACHE_REDIS_URL", "")
    monkeypatch.setattr(startup_checks, "is_production_env", lambda: True)

    async def _reachable() -> bool:
        return True

    async def _no_posture(_reachable: bool) -> None:
        return None

    async def _no_preflight() -> None:
        return None

    monkeypatch.setattr(startup_checks, "_probe_postgres", _reachable)
    monkeypatch.setattr(startup_checks, "_enforce_rls_posture", _no_posture)
    monkeypatch.setattr("core.services.llm.preflight.run_llm_preflight", _no_preflight)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url: _Engine())


async def test_up_to_date_database_is_recognised_outside_the_repo(
    production_db: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    (head,) = migration_heads()
    monkeypatch.setattr(
        migration.MigrationContext, "configure", staticmethod(lambda _c: _Ctx(head))
    )
    seen: list[str] = []
    monkeypatch.setattr(
        startup_checks.logger,
        "info",
        lambda msg, *args, **_kw: seen.append(msg % args if args else msg),
    )
    warned: list[str] = []
    monkeypatch.setattr(
        startup_checks.logger,
        "warning",
        lambda msg, *args, **_kw: warned.append(msg % args if args else msg),
    )

    with caplog.at_level(logging.INFO):
        await startup_checks.run_startup_health_checks()

    assert any(f"DB migrations up to date ({head})" in line for line in seen)
    assert not any("Could not verify migration status" in w for w in warned)
