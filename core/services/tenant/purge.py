"""Tenant data purge — GDPR right-to-be-forgotten.

Deletes every row scoped to a tenant across **all** public tables that carry a
``tenant_id`` column — core (``interactions``/``feedback``) and any plugin store
(BOP, pitwall, red_agent, …). The table set is discovered dynamically from
``information_schema`` so no hand-maintained list can drift out of date.

Foreign keys are handled by a **fixpoint** loop: a table whose delete fails
because a not-yet-purged child still references it is retried on the next pass,
until no further progress is made. The pool is autocommit, so a failed delete
never poisons later statements.

**Erasure must never quietly do nothing.** A purge runs as the ``system``
tenant, and ``DELETE`` is governed by a row-level-security policy's ``USING``
clause: rows the policy hides are not *refused*, they are simply not there, so
the statement reports ``0`` and the caller sees an erasure that "succeeded"
having deleted nothing. Migration ``010_system_tenant_rls_exemption`` is what
makes those rows visible to the system identity.

:func:`assert_purge_visible` is what refuses to let a blocked purge look like a
completed one. It works by **counting the rows under the target tenant's own
identity** before deleting them under ``system``: that count is taken by the one
session every ordinary policy is guaranteed to show those rows to, so
``expected > 0`` with ``deleted == 0`` is conclusive — the rows exist and the
maintenance identity could not reach them.

An earlier version asked the catalogue instead, checking that each table's
policy expression mentioned the system tenant. That was wrong for the input this
module actually has. It is applied to the tables discovered from
``information_schema``, which by design includes arbitrary plugin stores whose
policies this repository never wrote; and even on core's own tables a policy
reading ``… AND kind <> 'system'`` or a split ``FOR SELECT`` / ``FOR DELETE``
pair satisfies a substring test while the ``DELETE`` still removes nothing.
Counting observes the outcome rather than guessing at it from the schema.
"""

from __future__ import annotations

from core.context import reset_tenant_context, set_tenant_context
from core.db import connection as db_connection
from core.db.connection import get_async_cursor, system_tenant_scope
from core.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "TenantPurgeBlockedError",
    "assert_purge_visible",
    "purge_tenant_data",
    "tenant_scoped_tables",
]


class TenantPurgeBlockedError(RuntimeError):
    """The database hid the rows a purge was meant to delete.

    Raised instead of returning a row count of zero that cannot be trusted. The
    usual remedy is to apply migration ``010_system_tenant_rls_exemption``, which
    extends each ``tenant_isolation`` policy to admit the ``system`` tenant; a
    deployment with hand-written policies on plugin tables has to extend those
    itself, or grant the maintenance role ``BYPASSRLS``.

    A blocked purge is **partial**, so the exception carries what actually
    happened: an erasure that deleted four tables and was then blocked on the
    fifth is a different operational situation from one that deleted nothing,
    and a caller that can only report "it failed" forces someone to go and look.

    Attributes:
        purged: ``{table: rows_deleted}`` for the tables completed before the
            block — the same map a successful call returns.
        pending: Tables never attempted, or attempted and deferred by the
            foreign-key fixpoint loop. Re-running the purge after fixing the
            policy is safe: the deletes are idempotent.
    """

    def __init__(
        self,
        message: str,
        *,
        purged: dict[str, int] | None = None,
        pending: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.purged: dict[str, int] = dict(purged or {})
        self.pending: list[str] = list(pending or [])


def _rls_enabled() -> bool:
    """Whether row-level security is switched on for this deployment.

    Read through the module object so a test (and an operator reading the code)
    sees one source of truth. Unlike the CLI's copy in ``core.cli.handlers`` this
    needs no guarded lazy import: nothing in this module can run without
    ``core.db.connection`` already imported at the top.
    """
    return bool(db_connection.DB_RLS_ENABLED)


async def _rows_visible_to_tenant(table: str, tenant_id: str) -> int:
    """Count *tenant_id*'s rows in *table*, bound as that tenant.

    The session identity is the tenant being erased, so an ordinary
    ``tenant_id = current_setting(...)`` policy — the shape every core table and
    every conventional plugin store uses — shows exactly these rows. That is what
    makes the number a *floor* on what a correctly configured purge must delete.

    Args:
        table: A tenant-scoped table, from ``information_schema``.
        tenant_id: The tenant being erased.

    Returns:
        The number of rows the tenant itself can see, or ``0`` when the count
        could not be taken (a table the role cannot read at all, for instance —
        that is not evidence of hiding, so it must not block the purge).
    """
    token = set_tenant_context(tenant_id)
    try:
        async with get_async_cursor() as cur:
            # Same quoting rule as the DELETE below: the identifier comes from
            # information_schema, the value is parameterised.
            await cur.execute(
                f'SELECT count(*) FROM "{table}" WHERE tenant_id = %s',  # nosec B608
                (tenant_id,),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.debug("Tenant purge pre-count unavailable for %s: %s", table, exc)
        return 0
    finally:
        reset_tenant_context(token)


def assert_purge_visible(
    table: str, tenant_id: str, *, expected: int, deleted: int
) -> None:
    """Refuse to report a blocked erasure as a completed one.

    ``expected`` rows existed a moment ago, seen by the tenant's own session;
    ``deleted`` is what the ``DELETE`` under the maintenance identity actually
    removed. Rows present and none removed means the policy hid them from
    ``system``, and the caller has no other way to tell that from "there was
    nothing left to erase" — both are ``0``.

    Args:
        table: The table just purged.
        tenant_id: The tenant being erased.
        expected: Rows visible to the tenant before the delete.
        deleted: Rows the delete reported.

    Raises:
        TenantPurgeBlockedError: Rows existed and none were removed.
    """
    if expected <= 0 or deleted > 0:
        return
    raise TenantPurgeBlockedError(
        f"Purging tenant '{tenant_id}' removed 0 of {expected} row(s) from "
        f"'{table}': the maintenance identity cannot see them, so the erasure "
        "did not happen. Apply migration 010_system_tenant_rls_exemption, extend "
        f"that table's row-level-security policy to admit the '{tenant_id}' "
        "rows for maintenance, or grant the maintenance role BYPASSRLS."
    )


async def tenant_scoped_tables() -> list[str]:
    """Public tables that carry a ``tenant_id`` column.

    Runs inside :func:`core.db.connection.system_tenant_scope`: reading the
    catalogue is maintenance work with no request behind it, and under
    ``DB_RLS_ENABLED`` an unbound tenant is refused rather than degraded.
    """
    with system_tenant_scope():
        async with get_async_cursor() as cur:
            await cur.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND column_name = 'tenant_id' "
                "ORDER BY table_name"
            )
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def purge_tenant_data(tenant_id: str) -> dict[str, int]:
    """Delete all rows scoped to ``tenant_id`` across every tenant-scoped table.

    Returns a ``{table: rows_deleted}`` map. Idempotent (a second call deletes
    nothing). Tenant-scoped data only — the tenant entity row (``auth_tenants``)
    and membership are owned by the auth plugin's ``delete_tenant``.

    Runs inside :func:`core.db.connection.system_tenant_scope`. A purge is
    cross-tenant by construction — it deletes *another* tenant's rows — so it
    cannot be attributed to the tenant being erased, and under
    ``DB_RLS_ENABLED`` an unbound caller is refused outright. ``system`` is the
    identity a deployment grants the privileges this work needs.

    Raises:
        TenantPurgeBlockedError: A table held rows the tenant itself can see and
            the delete removed none of them, so the reported ``0`` would be an
            erasure that did not happen (see :func:`assert_purge_visible`). The
            purge is then **partial** — tables completed before the block stay
            deleted — and the exception carries ``purged`` and ``pending`` so the
            caller can report what happened rather than a bare failure. Fix the
            policy and re-run: every delete here is idempotent.
    """
    deleted: dict[str, int] = {}
    verify = _rls_enabled()
    with system_tenant_scope():
        tables = await tenant_scoped_tables()
        pending = set(tables)
        progress = True
        while pending and progress:
            progress = False
            for table in sorted(pending):
                # Taken before the delete and outside the try, as the tenant
                # rather than as ``system``: after the fact there is nothing left
                # to count, and a count under the maintenance identity would be
                # hidden by the very policy this is trying to detect.
                expected = (
                    await _rows_visible_to_tenant(table, tenant_id) if verify else 0
                )
                try:
                    async with get_async_cursor() as cur:
                        # table comes from information_schema (trusted), quoted
                        # as an identifier; the value is parameterised.
                        await cur.execute(
                            f'DELETE FROM "{table}" WHERE tenant_id = %s',  # nosec B608
                            (tenant_id,),
                        )
                        removed = cur.rowcount
                        deleted[table] = deleted.get(table, 0) + removed
                except Exception as exc:
                    logger.debug("Tenant purge retry for %s: %s", table, exc)
                    continue
                # Checked before the table leaves ``pending``, and outside the
                # broad handler above: a block is not a foreign-key deferral to
                # retry, and reporting it as pending is what makes the partial
                # outcome readable — ``pending`` means "not erased".
                try:
                    assert_purge_visible(
                        table, tenant_id, expected=expected, deleted=removed
                    )
                except TenantPurgeBlockedError as exc:
                    # Carry the partial outcome out with the failure. Without
                    # it the map of what WAS erased dies with the exception and
                    # the caller can only say "it failed".
                    exc.purged = dict(deleted)
                    exc.pending = sorted(pending)
                    raise
                pending.discard(table)
                progress = True
    if pending:
        logger.warning(
            "Tenant %s purge incomplete; tables still pending: %s",
            tenant_id,
            sorted(pending),
        )
    return deleted
