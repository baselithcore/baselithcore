"""RLS is only enforced if it applies to the role the pool authenticates as.

`DB_RLS_ENABLED=true` + migration 008 is not enough: a superuser, a `BYPASSRLS`
role, or the table owner without `FORCE ROW LEVEL SECURITY` all skip the policy
silently. These tests pin the three bypass vectors, the catalog probe that
reads them, and the production refusal that stops a deployment believing in
isolation it does not have.
"""

from __future__ import annotations

import pytest

from core.db.rls_posture import (
    ALLOW_BYPASS_ENV,
    RlsBypassError,
    RlsPosture,
    describe_rls_bypass,
    enforce_rls_posture,
    probe_rls_posture,
)

TABLES = ("interactions", "feedback")


# --------------------------------------------------------------------------
# Pure verdict
# --------------------------------------------------------------------------


def test_least_privilege_role_is_sound() -> None:
    """The documented posture — non-owner, non-super, policies on — passes."""
    assert describe_rls_bypass(RlsPosture(role="baselith_runtime")) is None


def test_superuser_is_reported() -> None:
    verdict = describe_rls_bypass(RlsPosture(role="postgres", is_superuser=True))
    assert verdict is not None
    assert "SUPERUSER" in verdict
    assert "postgres" in verdict


def test_bypassrls_attribute_is_reported() -> None:
    verdict = describe_rls_bypass(RlsPosture(role="app", bypasses_rls=True))
    assert verdict is not None
    assert "BYPASSRLS" in verdict


def test_ownership_without_force_is_reported() -> None:
    """The quiet one: not a superuser, policies on, and still exempt."""
    verdict = describe_rls_bypass(
        RlsPosture(role="baselithcore", owned_tables=("feedback", "interactions"))
    )
    assert verdict is not None
    assert "FORCE ROW LEVEL SECURITY" in verdict
    assert "feedback" in verdict


def test_policy_missing_on_a_table_is_reported() -> None:
    verdict = describe_rls_bypass(
        RlsPosture(role="baselith_runtime", unprotected_tables=("interactions",))
    )
    assert verdict is not None
    assert "interactions" in verdict
    assert "alembic upgrade head" in verdict


def test_absent_tables_are_not_a_fault() -> None:
    """A migration never run means no table, and a table with no rows to leak."""
    posture = RlsPosture(role="baselith_runtime", missing_tables=("tool_invocations",))
    assert describe_rls_bypass(posture) is None


def test_every_reason_is_reported_at_once() -> None:
    """One boot, one message: an operator fixes all of it in a single pass."""
    verdict = describe_rls_bypass(
        RlsPosture(
            role="postgres",
            is_superuser=True,
            bypasses_rls=True,
            owned_tables=("feedback",),
            unprotected_tables=("interactions",),
        )
    )
    assert verdict is not None
    for fragment in ("SUPERUSER", "BYPASSRLS", "FORCE ROW LEVEL SECURITY", "alembic"):
        assert fragment in verdict


def test_long_table_lists_are_capped() -> None:
    """The message goes in a log line, not a report."""
    verdict = describe_rls_bypass(
        RlsPosture(role="app", owned_tables=tuple(f"t{i}" for i in range(9)))
    )
    assert verdict is not None
    assert "(+4 more)" in verdict


# --------------------------------------------------------------------------
# Catalog probe
# --------------------------------------------------------------------------


class _FakeCursor:
    """Answers the two catalog queries the probe issues, in order."""

    def __init__(self, role_row, table_rows) -> None:
        self._role_row = role_row
        self._table_rows = table_rows
        self._last = ""

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def execute(self, sql: str, params: object = None) -> None:
        self._last = sql

    async def fetchone(self):
        return self._role_row

    async def fetchall(self):
        return self._table_rows


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _FakeCursor:
        return self._cursor


async def test_probe_reads_role_attributes_and_ownership() -> None:
    cursor = _FakeCursor(
        role_row=("baselithcore", True, False),
        # (relname, relrowsecurity, relforcerowsecurity, is_owner)
        table_rows=[
            ("interactions", True, False, True),
            ("feedback", True, True, True),
        ],
    )
    posture = await probe_rls_posture(_FakeConn(cursor), TABLES)

    assert posture.role == "baselithcore"
    assert posture.is_superuser is True
    assert posture.bypasses_rls is False
    # Owned and not forced → exempt. Owned *and* forced → the policy applies.
    assert posture.owned_tables == ("interactions",)
    assert posture.unprotected_tables == ()
    assert posture.missing_tables == ()


async def test_probe_flags_a_table_without_rls() -> None:
    cursor = _FakeCursor(
        role_row=("app", False, False),
        table_rows=[("interactions", False, False, False)],
    )
    posture = await probe_rls_posture(_FakeConn(cursor), TABLES)

    assert posture.unprotected_tables == ("interactions",)
    # RLS off shadows ownership: there is no policy to be exempt from.
    assert posture.owned_tables == ()
    assert posture.missing_tables == ("feedback",)


async def test_probe_on_a_clean_deployment_is_sound() -> None:
    cursor = _FakeCursor(
        role_row=("baselith_runtime", False, False),
        table_rows=[
            ("interactions", True, False, False),
            ("feedback", True, False, False),
        ],
    )
    posture = await probe_rls_posture(_FakeConn(cursor), TABLES)
    assert describe_rls_bypass(posture) is None


# --------------------------------------------------------------------------
# Startup enforcement
# --------------------------------------------------------------------------


class _Storage:
    def __init__(self, postgres_enabled: bool, db_rls_enabled: bool) -> None:
        self.postgres_enabled = postgres_enabled
        self.db_rls_enabled = db_rls_enabled


def _arrange(monkeypatch, *, storage: _Storage, posture: RlsPosture | None) -> None:
    """Point the enforcement at a fake storage config and a fake probe."""
    import core.config as config_module
    import core.db.rls_posture as module

    monkeypatch.setattr(
        config_module, "get_storage_config", lambda: storage, raising=False
    )

    async def _probe(_conn, tables=()):  # pragma: no cover - guarded by caller
        assert posture is not None
        return posture

    monkeypatch.setattr(module, "probe_rls_posture", _probe)

    class _Scope:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *exc: object) -> bool:
            return False

    class _Conn:
        async def __aenter__(self) -> _Conn:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

    import core.db.connection as conn_module

    monkeypatch.setattr(conn_module, "system_tenant_scope", lambda: _Scope())
    monkeypatch.setattr(conn_module, "get_async_connection", lambda: _Conn())


def _capture_errors(monkeypatch) -> list[str]:
    """Collect the module's ERROR lines.

    Asserted on the logger rather than on captured stdout: this logger is
    structlog, which renders to stdout through a configuration other tests in
    the suite reconfigure — so a capsys assertion passes alone and fails in a
    full run, for a reason that has nothing to do with the code under test.
    """
    import core.db.rls_posture as module

    messages: list[str] = []

    def _record(template: str, *args: object) -> None:
        messages.append(template % args if args else template)

    monkeypatch.setattr(module.logger, "error", _record)
    return messages


async def test_enforcement_is_a_noop_when_rls_is_off(monkeypatch) -> None:
    """Nothing was claimed, so there is nothing to contradict."""
    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=True, db_rls_enabled=False),
        posture=None,
    )
    await enforce_rls_posture()


async def test_enforcement_is_a_noop_without_postgres(monkeypatch) -> None:
    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=False, db_rls_enabled=True),
        posture=None,
    )
    await enforce_rls_posture()


async def test_production_refuses_a_bypassing_role(monkeypatch) -> None:
    """The whole point: a deployment that believes in isolation must not boot
    quietly without it."""
    import core.utils.runtime_env as runtime_env

    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=True, db_rls_enabled=True),
        posture=RlsPosture(role="postgres", is_superuser=True),
    )
    monkeypatch.setattr(runtime_env, "is_production_env", lambda: True)
    monkeypatch.delenv(ALLOW_BYPASS_ENV, raising=False)

    with pytest.raises(RlsBypassError) as excinfo:
        await enforce_rls_posture()
    assert "SUPERUSER" in str(excinfo.value)
    assert ALLOW_BYPASS_ENV in str(excinfo.value)


async def test_production_opt_out_downgrades_to_an_error_log(monkeypatch) -> None:
    import core.utils.runtime_env as runtime_env

    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=True, db_rls_enabled=True),
        posture=RlsPosture(role="postgres", is_superuser=True),
    )
    monkeypatch.setattr(runtime_env, "is_production_env", lambda: True)
    monkeypatch.setenv(ALLOW_BYPASS_ENV, "true")
    errors = _capture_errors(monkeypatch)

    await enforce_rls_posture()
    assert any("SUPERUSER" in message for message in errors)


async def test_outside_production_it_only_logs(monkeypatch) -> None:
    import core.utils.runtime_env as runtime_env

    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=True, db_rls_enabled=True),
        posture=RlsPosture(role="postgres", is_superuser=True),
    )
    monkeypatch.setattr(runtime_env, "is_production_env", lambda: False)
    monkeypatch.delenv(ALLOW_BYPASS_ENV, raising=False)
    errors = _capture_errors(monkeypatch)

    await enforce_rls_posture()
    assert any("SUPERUSER" in message for message in errors)


async def test_a_sound_posture_passes_in_production(monkeypatch) -> None:
    import core.utils.runtime_env as runtime_env

    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=True, db_rls_enabled=True),
        posture=RlsPosture(role="baselith_runtime"),
    )
    monkeypatch.setattr(runtime_env, "is_production_env", lambda: True)
    await enforce_rls_posture()


async def test_an_unreachable_database_never_blocks_boot(monkeypatch) -> None:
    """A transient outage is the health check's business, not a refusal."""
    import core.db.connection as conn_module
    import core.utils.runtime_env as runtime_env

    _arrange(
        monkeypatch,
        storage=_Storage(postgres_enabled=True, db_rls_enabled=True),
        posture=RlsPosture(role="baselith_runtime"),
    )
    monkeypatch.setattr(runtime_env, "is_production_env", lambda: True)

    def _boom():
        raise RuntimeError("connection refused")

    monkeypatch.setattr(conn_module, "get_async_connection", _boom)
    await enforce_rls_posture()
