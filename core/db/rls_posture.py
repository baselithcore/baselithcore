"""Does row-level security actually apply to the role the app connects as?

``DB_RLS_ENABLED=true`` plus migration 008 puts three things in place: the pool
binds ``app.tenant_id`` on every checkout, every tenant-scoped table carries a
policy, and the policy reads that GUC. None of it means isolation is *enforced*.
PostgreSQL skips a policy entirely for

* a ``SUPERUSER`` role — policies never apply to one;
* a role carrying the ``BYPASSRLS`` attribute;
* the **table owner**, unless the table also has ``FORCE ROW LEVEL SECURITY``
  (migration 008 deliberately leaves that off, so migrations and un-tenanted
  maintenance keep working — see its module docstring).

The shipped compose stack connects the app as ``POSTGRES_USER``, which is both
a superuser and the owner of every table. A deployment can therefore have RLS
switched on in three places and no isolation whatsoever, with nothing in the
logs saying so — the failure mode this module exists to make impossible.

The split mirrors :mod:`core.config._security_posture`: :func:`probe_rls_posture`
does the I/O, :func:`describe_rls_bypass` is pure and decides whether what came
back is a bypass, so the verdict is testable without a database.

Role *attributes* are never inherited through role membership in PostgreSQL, so
reading the attributes of ``current_user`` describes the session as it actually
runs. A member that could ``SET ROLE`` to a superuser is out of scope: that is a
grant to audit, not a silently-bypassed policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from core.db.ddl import RLS_PROTECTED_TABLES
from core.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psycopg import AsyncConnection

logger = get_logger(__name__)

__all__ = [
    "RlsBypassError",
    "RlsPosture",
    "describe_rls_bypass",
    "enforce_rls_posture",
    "probe_rls_posture",
]

#: Auditable opt-out, mirroring ``BASELITH_ALLOW_UNVALIDATED_HOST``: it
#: downgrades the production refusal below to an ERROR log for a deployment
#: that knows why its role bypasses RLS (a single-tenant install that turned
#: the switch on for the GUC alone, say).
ALLOW_BYPASS_ENV = "BASELITH_ALLOW_RLS_BYPASS"


class RlsBypassError(RuntimeError):
    """Production refused to start with row-level security not enforced."""


class _Cursor(Protocol):
    """The slice of a psycopg async cursor this module uses.

    Deliberately typed with ``Any`` at the edges: psycopg's ``AsyncCursor``
    returns itself from ``execute`` and ``list[Any]`` from ``fetchall``, and
    ``list`` is invariant — a stricter row type here would reject the very
    cursor this module is written against, for no safety gained on two
    catalog queries whose shape the functions below assert immediately.
    """

    async def execute(self, query: str, params: Any = ..., /) -> Any: ...

    async def fetchone(self) -> Any: ...

    async def fetchall(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class RlsPosture:
    """What the database says about the session's ability to bypass RLS.

    Attributes:
        role: The role the pool authenticates as (``current_user``).
        is_superuser: The role carries ``SUPERUSER``; every policy is skipped.
        bypasses_rls: The role carries ``BYPASSRLS``; every policy is skipped.
        owned_tables: Protected tables this role owns *without*
            ``FORCE ROW LEVEL SECURITY`` — their policies are skipped for it.
        unprotected_tables: Protected tables that exist but have no RLS
            enabled at all (migration 008/009 not applied, or reverted).
        missing_tables: Protected tables absent from the schema. Not a fault:
            a deployment that never ran a feature's migration simply has no
            such table, and it carries no rows to leak.
    """

    role: str
    is_superuser: bool = False
    bypasses_rls: bool = False
    owned_tables: tuple[str, ...] = ()
    unprotected_tables: tuple[str, ...] = ()
    missing_tables: tuple[str, ...] = field(default=())


def describe_rls_bypass(posture: RlsPosture) -> str | None:
    """Explain how ``posture`` defeats row-level security, if it does.

    Args:
        posture: A posture read back from the database.

    Returns:
        A single operator-facing sentence naming every reason isolation is not
        enforced and how to fix it, or ``None`` when the posture is sound.
    """
    reasons: list[str] = []
    if posture.is_superuser:
        reasons.append(
            f"the role {posture.role!r} is a SUPERUSER (policies never apply to one)"
        )
    if posture.bypasses_rls:
        reasons.append(f"the role {posture.role!r} carries BYPASSRLS")
    if posture.owned_tables:
        reasons.append(
            f"the role {posture.role!r} owns {_join(posture.owned_tables)} "
            "without FORCE ROW LEVEL SECURITY, so their policies are skipped "
            "for it"
        )
    if posture.unprotected_tables:
        reasons.append(
            f"row-level security is not enabled on "
            f"{_join(posture.unprotected_tables)} (run `alembic upgrade head`)"
        )
    if not reasons:
        return None
    return (
        "DB_RLS_ENABLED=true but the database does not enforce tenant "
        "isolation: " + "; ".join(reasons) + ". Connect the application as a "
        "least-privilege role — CREATE ROLE baselith_runtime LOGIN PASSWORD "
        "'…' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS, granted "
        "SELECT/INSERT/UPDATE/DELETE on the schema — and keep migrations "
        "running as the owner. See docs/architecture/tenant-rls.md."
    )


def _join(names: tuple[str, ...]) -> str:
    """Render a table tuple for a log line, capped so it stays readable."""
    shown = ", ".join(names[:5])
    remaining = len(names) - 5
    return f"{shown} (+{remaining} more)" if remaining > 0 else shown


async def probe_rls_posture(
    conn: AsyncConnection[object],
    tables: tuple[str, ...] = RLS_PROTECTED_TABLES,
) -> RlsPosture:
    """Read the session's RLS-bypass posture from the catalogs.

    Args:
        conn: An open connection, already bound to whatever tenant the caller
            is running as. Only catalog reads are issued.
        tables: Protected table names to inspect. Defaults to
            :data:`core.db.ddl.RLS_PROTECTED_TABLES`.

    Returns:
        The posture as the database reports it.
    """
    async with conn.cursor() as cur:
        role, is_superuser, bypasses = await _probe_role(cur)
        owned, unprotected, present = await _probe_tables(cur, tables)

    return RlsPosture(
        role=role,
        is_superuser=is_superuser,
        bypasses_rls=bypasses,
        owned_tables=owned,
        unprotected_tables=unprotected,
        missing_tables=tuple(t for t in tables if t not in present),
    )


async def _probe_role(cur: _Cursor) -> tuple[str, bool, bool]:
    """Read ``current_user`` and its two RLS-defeating attributes."""
    await cur.execute(
        "SELECT current_user::text, "
        "       COALESCE(r.rolsuper, false), "
        "       COALESCE(r.rolbypassrls, false) "
        "  FROM pg_roles r "
        " WHERE r.rolname = current_user"
    )
    row = await cur.fetchone()
    if row is None:  # pragma: no cover - current_user is always in pg_roles
        return ("unknown", False, False)
    return (str(row[0]), bool(row[1]), bool(row[2]))


async def _probe_tables(
    cur: _Cursor,
    tables: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...], set[str]]:
    """Read RLS state and ownership for ``tables`` in the search path.

    Returns:
        ``(owned_without_force, rls_disabled, present)`` — the first two are
        sorted tuples of table names, the third the set that exists at all.
    """
    if not tables:
        return ((), (), set())

    await cur.execute(
        "SELECT c.relname::text, "
        "       c.relrowsecurity, "
        "       c.relforcerowsecurity, "
        "       pg_get_userbyid(c.relowner) = current_user "
        "  FROM pg_class c "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        " WHERE c.relkind = 'r' "
        "   AND c.relname = ANY(%s) "
        "   AND n.nspname = ANY(current_schemas(false))",
        (list(tables),),
    )
    rows = await cur.fetchall()

    present: set[str] = set()
    owned: list[str] = []
    unprotected: list[str] = []
    for name, rls_on, forced, is_owner in rows:
        table = str(name)
        present.add(table)
        if not bool(rls_on):
            unprotected.append(table)
            continue
        if bool(is_owner) and not bool(forced):
            owned.append(table)

    return (tuple(sorted(owned)), tuple(sorted(unprotected)), present)


async def enforce_rls_posture() -> None:
    """Verify at startup that an enabled RLS actually applies to this session.

    No-op unless ``DB_RLS_ENABLED`` is on: with it off the framework never
    claimed database-level isolation, and there is nothing to contradict.

    Raises:
        RlsBypassError: Production, ``DB_RLS_ENABLED=true``, a posture that
            defeats the policies, and no explicit opt-out. Failing the boot is
            the point — a deployment that believes the database is isolating
            tenants and is wrong has no other moment to find out.
    """
    import os

    from core.config import get_storage_config

    storage = get_storage_config()
    if not getattr(storage, "postgres_enabled", False):
        return
    if not getattr(storage, "db_rls_enabled", False):
        return

    try:
        from core.db.connection import get_async_connection, system_tenant_scope

        # Startup has no request, so the pool would refuse an unbound checkout
        # under exactly the switch this function is here to verify.
        with system_tenant_scope():
            async with get_async_connection() as conn:
                posture = await probe_rls_posture(conn)
    except Exception as exc:
        # An unreachable database is already reported by the startup health
        # check at ERROR level in production; do not report it twice, and do
        # not turn a transient outage into a refusal to boot.
        logger.warning(
            "Row-level-security posture check skipped (%s: %s); tenant "
            "isolation at the database has not been verified.",
            type(exc).__name__,
            exc,
        )
        return

    problem = describe_rls_bypass(posture)
    if problem is None:
        logger.info(
            "🔒 Row-level security enforced for role %r on %d tenant-scoped table(s).",
            posture.role,
            len(RLS_PROTECTED_TABLES) - len(posture.missing_tables),
        )
        return

    from core.utils.runtime_env import is_production_env

    opted_out = os.getenv(ALLOW_BYPASS_ENV, "").lower() in ("1", "true", "yes", "on")
    if is_production_env() and not opted_out:
        raise RlsBypassError(
            f"🔒 {problem} Set {ALLOW_BYPASS_ENV}=true to accept the risk explicitly."
        )
    logger.error("🔒 %s", problem)
