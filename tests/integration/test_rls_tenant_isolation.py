"""Integration test: row-level security actually isolates tenants.

Runs only against a **real** PostgreSQL, and only with the real-database opt-in::

    docker compose up -d postgres
    BASELITH_TEST_REAL_DB=1 python -m pytest tests/integration/test_rls_tenant_isolation.py

(``tests/conftest.py`` mocks psycopg globally for the fast unit run; without the
flag every case here skips.)

The test connects as a **dedicated least-privilege role**, not as the pool's
configured user, and that is the whole point. Postgres exempts two kinds of
session from row-level security:

* a **superuser** — which is what ``POSTGRES_USER`` is in the compose stack, so
  a test using the normal pool would pass vacuously while proving nothing;
* the **table owner**, unless the table is set to ``FORCE ROW LEVEL SECURITY``.

The default single-role deployment is both. So the policy shipped in
``migrations/versions/008_row_level_security.py`` changes nothing until the
deployment separates the roles — exactly as that migration's docstring and the
multi-tenancy guide state. This test creates that separation and proves the
policy is correct under it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from core.db.ddl import RLS_PROTECTED_TABLES
from core.db.session_setup import SYSTEM_TENANT_ID

pytestmark = [pytest.mark.integration]

TABLE = "agent_patterns"
POLICY = "tenant_isolation"


def _migration_010() -> Any:
    """The live exemption migration, loaded by path (it is not importable)."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "versions"
        / "010_system_tenant_rls_exemption.py"
    )
    spec = importlib.util.spec_from_file_location("rls_it_010", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _shipped_predicate() -> str:
    """The predicate the schema currently ships, from the migration that wrote it."""
    return str(_migration_010()._PREDICATE)


RUNTIME_ROLE = "rls_test_runtime"
RUNTIME_PASSWORD = "rls_test_pw"
#: Every row this module writes carries it, so teardown can clean up precisely.
MARKER = "rls-itest"


def _owner_conninfo() -> str:
    """The pool's target database, reached as the configured (owner) role."""
    from core.config import get_storage_config

    config = get_storage_config()
    return (
        f"postgresql://{config.db_user}:{config.db_password.get_secret_value()}"
        f"@{config.db_host}:{config.db_port}/{config.db_name}"
    )


def _pg_available() -> bool:
    """True only against a real PostgreSQL.

    Connects with psycopg directly rather than through the shared pool: these
    tests are synchronous, and the pool is mocked in the default unit run.
    """
    try:
        import psycopg

        with (
            psycopg.connect(_owner_conninfo(), connect_timeout=3) as conn,
            conn.cursor() as cur,
        ):
            cur.execute("SELECT 1")
            row = cur.fetchone()
        return bool(row and row[0] == 1)
    except Exception:
        return False


def _runtime_conninfo() -> str:
    """The pool's target database, reached as the least-privilege role."""
    from core.config import get_storage_config

    config = get_storage_config()
    return (
        f"postgresql://{RUNTIME_ROLE}:{RUNTIME_PASSWORD}"
        f"@{config.db_host}:{config.db_port}/{config.db_name}"
    )


@pytest.fixture
def rls_ready() -> Iterator[None]:
    """Shipped table + shipped policy + a non-owner, non-superuser role."""
    if not _pg_available():
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")

    import psycopg

    with (
        psycopg.connect(_owner_conninfo(), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL DEFAULT 'default',
                fingerprint TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
                occurrences INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'candidate',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        # The policy under test, taken from the migration itself rather than
        # retyped: a hard-coded copy of 008's predicate kept passing after
        # migration 010 widened it, i.e. it tested a policy the schema no longer
        # ships. Importing the module means the test can only ever be wrong in
        # the same direction as production.
        cur.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        cur.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
        cur.execute(
            f"CREATE POLICY {POLICY} ON {TABLE} "
            f"USING {_shipped_predicate()} "
            f"WITH CHECK {_shipped_predicate()}"
        )
        # The two-role deployment this migration is designed for, in miniature.
        cur.execute(
            "DO $$ BEGIN "
            f"  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{RUNTIME_ROLE}') THEN "
            f"    DROP OWNED BY {RUNTIME_ROLE}; DROP ROLE {RUNTIME_ROLE}; "
            "  END IF; "
            "END $$"
        )
        cur.execute(
            f"CREATE ROLE {RUNTIME_ROLE} LOGIN PASSWORD '{RUNTIME_PASSWORD}' "
            "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
        )
        # USAGE on the schema is not implicit: a role without it cannot even
        # resolve the table name (Postgres reports "relation does not exist",
        # not a permission error). Part of the two-role recipe in the
        # multi-tenancy guide.
        cur.execute(f"GRANT USAGE ON SCHEMA public TO {RUNTIME_ROLE}")
        cur.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {RUNTIME_ROLE}"
        )

    yield

    with (
        psycopg.connect(_owner_conninfo(), autocommit=True) as conn,
        conn.cursor() as cur,
    ):
        cur.execute(f"DELETE FROM {TABLE} WHERE fingerprint LIKE %s", (f"{MARKER}%",))
        # DROP OWNED BY is the canonical way to detach a role: a plain REVOKE
        # leaves per-object grants behind and DROP ROLE then fails with
        # "objects depend on it".
        cur.execute(f"DROP OWNED BY {RUNTIME_ROLE}")
        cur.execute(f"DROP ROLE IF EXISTS {RUNTIME_ROLE}")


@pytest.fixture
def runtime_connect() -> Iterator[Any]:
    """Open a connection as the least-privilege role, with a bound tenant."""
    import psycopg

    opened: list[Any] = []

    def _connect(tenant: str) -> Any:
        conn = psycopg.connect(_runtime_conninfo(), autocommit=True)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        opened.append(conn)
        return conn

    yield _connect

    for conn in opened:
        conn.close()


def _insert(conn: Any, tenant: str, title: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO {TABLE} (id, tenant_id, fingerprint, kind, title, summary) "
            "VALUES (%s, %s, %s, 'test', %s, 'summary')",
            (f"{MARKER}-{uuid.uuid4().hex[:10]}", tenant, f"{MARKER}-{title}", title),
        )


class TestRowLevelSecurity:
    def test_a_tenant_sees_only_its_own_rows(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"

        conn_a = runtime_connect(tenant_a)
        conn_b = runtime_connect(tenant_b)
        _insert(conn_a, tenant_a, "mine")
        _insert(conn_b, tenant_b, "theirs")

        with conn_a.cursor() as cur:
            cur.execute(f"SELECT DISTINCT tenant_id FROM {TABLE}")
            visible = {row[0] for row in cur.fetchall()}

        assert visible == {tenant_a}, "tenant A saw rows outside its own tenant"

    def test_a_forgotten_where_clause_still_isolates(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        """The point of RLS: the Python-side tenant predicate is not required."""
        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"

        conn_a = runtime_connect(tenant_a)
        conn_b = runtime_connect(tenant_b)
        _insert(conn_a, tenant_a, "mine")
        _insert(conn_b, tenant_b, "theirs")
        _insert(conn_b, tenant_b, "theirs-again")

        with conn_a.cursor() as cur:
            # Deliberately no `WHERE tenant_id = ...`: the bug RLS exists to stop.
            cur.execute(f"SELECT count(*) FROM {TABLE} WHERE kind = 'test'")
            row = cur.fetchone()

        assert row is not None and row[0] == 1

    def test_writing_another_tenants_row_is_refused(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        """``WITH CHECK`` blocks the cross-tenant write, not just the read."""
        import psycopg

        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"
        conn_a = runtime_connect(tenant_a)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(conn_a, tenant_b, "smuggled")

    def test_updating_a_row_into_another_tenant_is_refused(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        import psycopg

        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"
        conn_a = runtime_connect(tenant_a)
        _insert(conn_a, tenant_a, "mine")

        with (
            pytest.raises(psycopg.errors.InsufficientPrivilege),
            conn_a.cursor() as cur,
        ):
            cur.execute(f"UPDATE {TABLE} SET tenant_id = %s", (tenant_b,))

    def test_an_unbound_session_falls_back_to_the_default_tenant(
        self, rls_ready: None
    ) -> None:
        """No ``app.tenant_id`` must not mean "see everything"."""
        import psycopg

        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(_runtime_conninfo(), autocommit=True) as bound:
            with bound.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.tenant_id', %s, false)", (tenant_a,)
                )
            _insert(bound, tenant_a, "mine")

        with psycopg.connect(_runtime_conninfo(), autocommit=True) as unbound:
            with unbound.cursor() as cur:
                cur.execute(f"SELECT count(*) FROM {TABLE} WHERE kind = 'test'")
                row = cur.fetchone()

        assert row is not None and row[0] == 0, (
            "a session with no app.tenant_id saw another tenant's rows"
        )


class TestSystemTenantExemption:
    """Migration 010: maintenance work must see and write every tenant's rows.

    Under 008's predicate the ``system`` identity matched nothing, so the crash
    recovery sweep discovered no interrupted runs and — worse — a GDPR purge
    issued its ``DELETE`` against rows the ``USING`` clause hid and reported a
    truthful ``0`` that read as a completed erasure.
    """

    def test_the_system_tenant_sees_every_tenants_rows(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"
        _insert(runtime_connect(tenant_a), tenant_a, "mine")
        _insert(runtime_connect(tenant_b), tenant_b, "theirs")

        with runtime_connect(SYSTEM_TENANT_ID).cursor() as cur:
            cur.execute(f"SELECT DISTINCT tenant_id FROM {TABLE} WHERE kind = 'test'")
            visible = {row[0] for row in cur.fetchall()}

        assert {tenant_a, tenant_b} <= visible

    def test_the_system_tenant_can_delete_another_tenants_rows(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        """The purge path: ``DELETE`` is governed by ``USING``, and a hidden row
        is not refused — it is simply absent, so the count is a silent zero."""
        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        _insert(runtime_connect(tenant_a), tenant_a, "doomed")

        with runtime_connect(SYSTEM_TENANT_ID).cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE} WHERE tenant_id = %s", (tenant_a,))
            removed = cur.rowcount

        assert removed == 1, "the purge identity deleted nothing and said so quietly"

    def test_the_system_tenant_can_write_back_another_tenants_row(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        """``WITH CHECK``: the stale-run sweep re-sends the row's own tenant_id
        through an ``INSERT … ON CONFLICT DO UPDATE``."""
        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        _insert(runtime_connect(tenant_a), tenant_a, "wedged")

        with runtime_connect(SYSTEM_TENANT_ID).cursor() as cur:
            cur.execute(
                f"UPDATE {TABLE} SET status = 'failed' WHERE tenant_id = %s",
                (tenant_a,),
            )
            assert cur.rowcount == 1

    def test_an_ordinary_tenant_is_not_widened_by_the_exemption(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        """The escape compares the *session*, so it can only ever relax things
        for ``system``. This is the regression that would matter most."""
        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"
        _insert(runtime_connect(tenant_a), tenant_a, "mine")
        _insert(runtime_connect(tenant_b), tenant_b, "theirs")

        with runtime_connect(tenant_a).cursor() as cur:
            cur.execute(f"SELECT DISTINCT tenant_id FROM {TABLE}")
            visible = {row[0] for row in cur.fetchall()}

        assert visible == {tenant_a}

    def test_an_ordinary_tenant_still_cannot_write_another_tenants_row(
        self, rls_ready: None, runtime_connect: Any
    ) -> None:
        import psycopg

        tenant_a = f"rls-a-{uuid.uuid4().hex[:6]}"
        tenant_b = f"rls-b-{uuid.uuid4().hex[:6]}"

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(runtime_connect(tenant_a), tenant_b, "smuggled")


def test_every_protected_table_is_named_in_the_policy_list() -> None:
    """Cheap guard that runs without Postgres: the list is not empty or stale."""
    assert TABLE in RLS_PROTECTED_TABLES
    assert len(RLS_PROTECTED_TABLES) >= 6


def test_the_test_uses_the_predicate_the_schema_ships() -> None:
    """Also runs without Postgres. The previous version hard-coded 008's
    predicate, so after 010 it exercised a policy production no longer has."""
    predicate = _shipped_predicate()
    assert f"= '{SYSTEM_TENANT_ID}'" in predicate
    assert "current_setting('app.tenant_id', true)" in predicate
