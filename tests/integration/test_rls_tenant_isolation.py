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

pytestmark = [pytest.mark.integration]

TABLE = "agent_patterns"
POLICY = "tenant_isolation"
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
        # The policy under test, verbatim from 008_row_level_security.py.
        cur.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        cur.execute(f"DROP POLICY IF EXISTS {POLICY} ON {TABLE}")
        cur.execute(
            f"CREATE POLICY {POLICY} ON {TABLE} "
            "USING (tenant_id = COALESCE(current_setting('app.tenant_id', true), 'default')) "
            "WITH CHECK (tenant_id = COALESCE(current_setting('app.tenant_id', true), 'default'))"
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


def test_every_protected_table_is_named_in_the_policy_list() -> None:
    """Cheap guard that runs without Postgres: the list is not empty or stale."""
    assert TABLE in RLS_PROTECTED_TABLES
    assert len(RLS_PROTECTED_TABLES) >= 6
