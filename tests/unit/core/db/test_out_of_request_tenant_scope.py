"""Out-of-request DB paths must declare a tenant under RLS.

With ``DB_RLS_ENABLED=true`` the pool refuses to invent a tenant
(:mod:`tests.unit.core.db.test_rls_tenant_binding`), so anything that opens a
connection without a request behind it has to say what it is. The API boot path
and the RQ worker already did; these paths did not, and raised
``TenantContextError`` on the first connection checkout:

* :func:`core.services.tenant.purge.purge_tenant_data` — cross-tenant by
  construction, so it cannot borrow the tenant it is erasing;
* the self-initializing stores' idempotent DDL, whose first touch is routinely
  an import-time bootstrap, a worker or a script.

Each test asserts the tenant that is bound *at the moment the cursor is opened*
— which is exactly what ``_current_tenant_for_session`` reads — and that the
caller's own context is restored afterwards.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from core import context as core_context
from core.context import get_current_tenant_id
from core.db import connection as db_connection
from core.db.session_setup import SYSTEM_TENANT_ID
from core.services.tenant import purge as purge_module
from core.services.tenant.purge import TenantPurgeBlockedError

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _no_store_purge():
    """These tests cover the SQL path; the vector/cache purge has its own."""
    from core.services.tenant import purge_stores

    with patch.object(
        purge_stores,
        "purge_tenant_stores",
        AsyncMock(return_value=purge_stores.TenantStoresPurge()),
    ):
        yield


@contextmanager
def no_tenant():
    """Unbind the tenant contextvar (the autouse fixture binds ``default``)."""
    token = core_context._tenant_context.set(None)
    try:
        yield
    finally:
        core_context._tenant_context.reset(token)


def _cursor_ctx(cursor):
    """A ``get_async_cursor`` stand-in yielding a fixed cursor."""

    @asynccontextmanager
    async def ctx(*args, **kwargs):
        yield cursor

    return ctx


def _recording_cursor() -> tuple[MagicMock, list[str | None]]:
    """A cursor factory that records the tenant bound on every checkout."""
    seen: list[str | None] = []
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.rowcount = 0

    @asynccontextmanager
    async def factory(*args, **kwargs):
        seen.append(core_context._tenant_context.get())
        yield cursor

    cursor.factory = factory
    return cursor, seen


class TestTenantPurge:
    async def test_purge_runs_as_the_system_tenant(self):
        cursor, seen = _recording_cursor()
        cursor.fetchall = AsyncMock(return_value=[("interactions",)])
        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", cursor.factory),
        ):
            from core.services.tenant.purge import purge_tenant_data

            await purge_tenant_data("doomed-tenant")

        assert seen, "no connection was opened"
        assert set(seen) == {SYSTEM_TENANT_ID}

    async def test_table_discovery_runs_as_the_system_tenant(self):
        cursor, seen = _recording_cursor()
        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", cursor.factory),
        ):
            from core.services.tenant.purge import tenant_scoped_tables

            await tenant_scoped_tables()

        assert seen == [SYSTEM_TENANT_ID]

    async def test_the_callers_tenant_is_restored(self):
        cursor, seen = _recording_cursor()
        cursor.fetchall = AsyncMock(return_value=[])
        with patch("core.services.tenant.purge.get_async_cursor", cursor.factory):
            from core.services.tenant.purge import purge_tenant_data

            await purge_tenant_data("doomed-tenant")

        assert get_current_tenant_id() == "default"


class TestSelfInitializingStores:
    """Idempotent DDL is deployment work, not a tenant's."""

    async def test_a2a_task_store(self):
        from core.a2a.task_store_postgres import PostgresTaskStore

        cursor, seen = _recording_cursor()
        with (
            no_tenant(),
            patch("core.a2a.task_store_postgres.get_async_cursor", cursor.factory),
            patch("core.a2a.task_store_postgres.skip_runtime_ddl", return_value=False),
        ):
            await PostgresTaskStore().initialize()

        assert seen == [SYSTEM_TENANT_ID]
        assert core_context._tenant_context.get() == "default"

    async def test_prompt_store(self):
        from core.prompts.store_postgres import PostgresPromptBackend

        cursor, seen = _recording_cursor()
        with (
            no_tenant(),
            patch("core.prompts.store_postgres.get_async_cursor", cursor.factory),
            patch("core.prompts.store_postgres.skip_runtime_ddl", return_value=False),
        ):
            await PostgresPromptBackend().initialize()

        assert seen == [SYSTEM_TENANT_ID]

    async def test_the_whole_prompt_store_is_scoped_not_just_its_ddl(self):
        """Prompts are deployment-global (no ``tenant_id`` column, no policy),
        and the reader is a background refresh loop. Unscoped, every tick raised
        into ``PromptSynchronizer.refresh``'s fail-open handler, so prompt sync
        logged a warning and silently never synced."""
        from core.prompts.store_postgres import PostgresPromptBackend

        cursor, seen = _recording_cursor()
        cursor.fetchall = AsyncMock(return_value=[])
        with (
            no_tenant(),
            patch("core.prompts.store_postgres.get_async_cursor", cursor.factory),
        ):
            versions, labels = await PostgresPromptBackend().fetch_all()

        assert (versions, labels) == ([], {})
        assert seen == [SYSTEM_TENANT_ID]

    async def test_checkpoint_store(self):
        from core.orchestration.checkpoint_postgres import PostgresCheckpointStore

        cursor, seen = _recording_cursor()
        with (
            no_tenant(),
            patch(
                "core.orchestration.checkpoint_postgres.get_async_cursor",
                cursor.factory,
            ),
            patch(
                "core.orchestration.checkpoint_postgres.skip_runtime_ddl",
                return_value=False,
            ),
        ):
            await PostgresCheckpointStore().initialize()

        assert seen == [SYSTEM_TENANT_ID]


class TestPurgeRefusesToBeInvisible:
    """A zero row count is only meaningful if the rows could have been seen.

    ``DELETE`` is governed by a policy's ``USING`` clause, so rows the policy
    hides are not refused — they are absent, and the statement reports ``0``.
    Against migration 008's policy the ``system`` identity a purge binds matched
    no real tenant's rows, so a GDPR erasure returned a *truthful* zero that read
    as success.

    The check counts the rows **as the tenant being erased** before deleting them
    as ``system``. That identity is the one every ordinary policy is guaranteed
    to show those rows to, so "the tenant can see N and the purge removed 0" is
    conclusive. It replaced a catalogue check that asked whether each table's
    policy expression mentioned the system tenant — falsifiable on exactly this
    module's input, since the table set comes from ``information_schema`` and
    includes plugin stores whose policies this repository never wrote.
    """

    @pytest.fixture
    def rls_on(self, monkeypatch):
        monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", True)

    @pytest.fixture
    def rls_off(self, monkeypatch):
        monkeypatch.setattr(db_connection, "DB_RLS_ENABLED", False)

    @staticmethod
    def _purge_cursor(*, visible: int, removed: int):
        """A cursor scripted for: discovery, pre-count, delete."""
        statements: list[tuple[str, str | None]] = []
        cursor = MagicMock()
        cursor.rowcount = removed

        async def _execute(sql, params=None):
            statements.append(
                (" ".join(str(sql).split()), core_context._tenant_context.get())
            )

        cursor.execute = AsyncMock(side_effect=_execute)
        cursor.fetchall = AsyncMock(return_value=[("interactions",)])
        cursor.fetchone = AsyncMock(return_value=(visible,))
        return cursor, statements

    async def test_rows_the_tenant_can_see_but_the_purge_cannot_are_a_failure(
        self, rls_on
    ):
        cursor, _ = self._purge_cursor(visible=2, removed=0)

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
            pytest.raises(TenantPurgeBlockedError, match="0 of 2"),
        ):
            await purge_module.purge_tenant_data("doomed-tenant")

    async def test_the_message_names_the_table_and_a_remedy(self, rls_on):
        cursor, _ = self._purge_cursor(visible=1, removed=0)

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
            pytest.raises(TenantPurgeBlockedError) as excinfo,
        ):
            await purge_module.purge_tenant_data("doomed-tenant")

        message = str(excinfo.value)
        assert "interactions" in message
        assert "010_system_tenant_rls_exemption" in message

    async def test_an_erasure_that_worked_is_not_flagged(self, rls_on):
        cursor, _ = self._purge_cursor(visible=2, removed=2)

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
        ):
            result = await purge_module.purge_tenant_data("doomed-tenant")

        assert result == {"interactions": 2}

    async def test_nothing_to_erase_is_not_an_error(self, rls_on):
        """Zero expected, zero deleted — the idempotent second purge."""
        cursor, _ = self._purge_cursor(visible=0, removed=0)

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
        ):
            assert await purge_module.purge_tenant_data("doomed") == {"interactions": 0}

    async def test_the_pre_count_is_taken_as_the_target_tenant(self, rls_on):
        """The whole mechanism: counted by the session the rows belong to, then
        deleted by the maintenance identity. A count under ``system`` would be
        hidden by the very policy this detects."""
        cursor, statements = self._purge_cursor(visible=1, removed=1)

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
        ):
            await purge_module.purge_tenant_data("doomed-tenant")

        counts = [t for sql, t in statements if sql.startswith("SELECT count(*)")]
        deletes = [t for sql, t in statements if sql.startswith("DELETE")]
        assert counts == ["doomed-tenant"]
        assert deletes == [SYSTEM_TENANT_ID]

    async def test_the_count_precedes_the_delete(self, rls_on):
        cursor, statements = self._purge_cursor(visible=1, removed=1)

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
        ):
            await purge_module.purge_tenant_data("doomed-tenant")

        kinds = [sql.split()[0] for sql, _ in statements]
        assert kinds.index("SELECT") < kinds.index("DELETE")

    async def test_it_costs_nothing_when_rls_is_off(self, rls_off):
        """Byte-identical to the pre-RLS path: no policy can hide anything, so
        no pre-count is issued."""
        cursor, statements = self._purge_cursor(visible=0, removed=0)

        with patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)):
            await purge_module.purge_tenant_data("doomed-tenant")

        assert not any(sql.startswith("SELECT count(*)") for sql, _ in statements)

    async def test_an_unreadable_table_does_not_block_the_purge(self, rls_on):
        """A count that cannot be taken is not evidence of hiding."""
        cursor, _ = self._purge_cursor(visible=0, removed=0)
        cursor.fetchone = AsyncMock(side_effect=RuntimeError("permission denied"))

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
        ):
            assert await purge_module.purge_tenant_data("doomed") == {"interactions": 0}

    async def test_a_blocked_purge_carries_what_it_did_erase(self, rls_on):
        """A blocked purge is partial. An erasure that cleared four tables and
        stopped on the fifth is a different situation from one that cleared
        nothing, and a caller that can only say "it failed" sends someone to go
        and look."""
        statements: list[str] = []
        cursor = MagicMock()

        async def _execute(sql, params=None):
            statements.append(" ".join(str(sql).split()))

        cursor.execute = AsyncMock(side_effect=_execute)
        cursor.fetchall = AsyncMock(return_value=[("feedback",), ("interactions",)])
        # feedback: 1 visible, 1 removed (fine). interactions: 1 visible, 0
        # removed (blocked). Sorted order puts feedback first.
        cursor.fetchone = AsyncMock(side_effect=[(1,), (1,)])
        type(cursor).rowcount = PropertyMock(side_effect=[1, 0])

        with (
            no_tenant(),
            patch("core.services.tenant.purge.get_async_cursor", _cursor_ctx(cursor)),
            pytest.raises(TenantPurgeBlockedError) as excinfo,
        ):
            await purge_module.purge_tenant_data("doomed-tenant")

        # feedback was erased; interactions reports the truthful 0 that the
        # blocked delete returned, and stays pending because it was not erased.
        assert excinfo.value.purged == {"feedback": 1, "interactions": 0}
        assert excinfo.value.pending == ["interactions"]

    async def test_the_partial_map_defaults_to_empty(self):
        """Constructed directly — the attributes always exist, so a caller never
        has to ``getattr`` its way around them."""
        error = TenantPurgeBlockedError("blocked")
        assert error.purged == {}
        assert error.pending == []
