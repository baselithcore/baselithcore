"""``DB_SCHEMA_CHECK``: production refuses to start on a stale schema.

The check used to log an ERROR and let startup continue, so a rollout whose
migrations had not run reported healthy and then failed requests on the first
missing column.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from core.api import _schema_check
from core.api._schema_check import (
    SchemaRevisionMismatchError,
    check_schema_revision,
    resolve_schema_check_mode,
)


class _Cfg:
    def __init__(self, mode: str | None) -> None:
        self.db_schema_check = mode


def _use(
    monkeypatch: pytest.MonkeyPatch, mode: str | None, revisions: Any
) -> list[int]:
    calls: list[int] = []

    def _read() -> tuple[str, str, bool]:
        calls.append(1)
        if isinstance(revisions, Exception):
            raise revisions
        if len(revisions) == 2:
            return (*revisions, False)
        return revisions

    monkeypatch.setattr(_schema_check, "get_storage_config", lambda: _Cfg(mode))
    monkeypatch.setattr(_schema_check, "_read_revisions", _read)
    return calls


@pytest.mark.parametrize(
    ("configured", "production", "expected"),
    [
        (None, True, "strict"),
        (None, False, "off"),
        ("warn", True, "warn"),
        ("off", True, "off"),
        ("strict", False, "strict"),
        ("warn", False, "warn"),
    ],
)
def test_mode_resolution(
    configured: str | None, production: bool, expected: str
) -> None:
    assert resolve_schema_check_mode(configured, is_production=production) == expected


async def test_production_default_refuses_a_stale_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, None, ("041_old", "042_new"))
    with pytest.raises(SchemaRevisionMismatchError) as excinfo:
        await check_schema_revision(is_production=True, postgres_reachable=True)
    message = str(excinfo.value)
    assert "current: 041_old" in message
    assert "head: 042_new" in message
    assert "baselith db migrate" in message
    assert "DB_SCHEMA_CHECK=warn" in message


async def test_warn_logs_and_continues(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _use(monkeypatch, "warn", ("041_old", "042_new"))
    errors: list[str] = []
    monkeypatch.setattr(
        _schema_check.logger, "error", lambda msg, *a, **_k: errors.append(msg)
    )
    await check_schema_revision(is_production=True, postgres_reachable=True)
    assert any("current: 041_old" in line for line in errors)


async def test_up_to_date_passes_in_strict(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, "strict", ("042_new", "042_new"))
    await check_schema_revision(is_production=True, postgres_reachable=True)


async def test_off_and_non_production_default_skip_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _use(monkeypatch, "off", ("a", "b"))
    await check_schema_revision(is_production=True, postgres_reachable=True)
    calls = _use(monkeypatch, None, ("a", "b"))
    await check_schema_revision(is_production=False, postgres_reachable=True)
    assert calls == []


async def test_explicit_strict_applies_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use(monkeypatch, "strict", ("a", "b"))
    with pytest.raises(SchemaRevisionMismatchError):
        await check_schema_revision(is_production=False, postgres_reachable=True)


async def test_a_check_that_cannot_run_only_warns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warned: list[str] = []
    monkeypatch.setattr(
        _schema_check.logger,
        "warning",
        lambda msg, *a, **_k: warned.append(msg % a if a else msg),
    )
    calls = _use(monkeypatch, "strict", ("a", "b"))
    await check_schema_revision(is_production=True, postgres_reachable=False)
    assert calls == []
    _use(monkeypatch, "strict", ConnectionError("refused"))
    await check_schema_revision(is_production=True, postgres_reachable=True)
    assert len(warned) == 2
    assert all("Could not verify migration status" in w for w in warned)


async def test_startup_health_checks_propagate_the_refusal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from core.api import startup_checks

    monkeypatch.setattr(startup_checks, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(startup_checks, "CACHE_REDIS_URL", "")
    monkeypatch.setattr(startup_checks, "is_production_env", lambda: True)

    async def _reachable() -> bool:
        return True

    async def _no_posture(_reachable: bool) -> None:
        return None

    monkeypatch.setattr(startup_checks, "_probe_postgres", _reachable)
    monkeypatch.setattr(startup_checks, "_enforce_rls_posture", _no_posture)
    _use(monkeypatch, None, ("041_old", "042_new"))
    with caplog.at_level(logging.INFO), pytest.raises(SchemaRevisionMismatchError):
        await startup_checks.run_startup_health_checks()


async def test_strict_starts_when_the_database_is_ahead_of_the_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rollback meets a schema a newer image migrated: warn, never refuse."""
    _use(monkeypatch, "strict", ("043_newer", "042_new", True))
    warnings: list[str] = []
    monkeypatch.setattr(
        _schema_check.logger, "warning", lambda msg, *a, **_k: warnings.append(msg)
    )
    await check_schema_revision(is_production=True, postgres_reachable=True)
    assert warnings and "newer than this package" in warnings[0]
