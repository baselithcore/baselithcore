"""Crash recovery has to declare an identity under row-level security.

The recovery sweep is started at boot (``core.api._recovery_startup``) and loops
forever on a timer. It is also cross-tenant by construction: it is looking for
*whose* runs were interrupted, so it cannot borrow the identity of a run it has
not found yet. With ``DB_RLS_ENABLED=true`` the pool refuses to invent a tenant,
so the discovery query raised ``TenantContextError`` straight into the cycle's
own fail-open handler — a ``recovery_cycle_failed`` warning every interval and
crash recovery that silently never ran, on exactly the deployments that turned
isolation on.

The stores below stand in for that pool: they refuse to answer unless a tenant
is bound, which is the whole behaviour under test.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest

from core import context as core_context
from core.context import TenantContextError, get_current_tenant_id
from core.db.session_setup import SYSTEM_TENANT_ID
from core.orchestration.checkpoint import (
    STATUS_FAILED,
    STATUS_RUNNING,
    Checkpoint,
    InMemoryCheckpointStore,
)
from core.orchestration.recovery import resume_interrupted_runs, sweep_stale_runs

pytestmark = [pytest.mark.unit]


@contextmanager
def no_tenant():
    """Unbind the tenant contextvar (the autouse fixture binds ``default``)."""
    token = core_context._tenant_context.set(None)
    try:
        yield
    finally:
        core_context._tenant_context.reset(token)


class _RlsCheckpointStore(InMemoryCheckpointStore):
    """An in-memory store that behaves like the pool under RLS.

    Every access records the tenant bound at the time and refuses outright when
    none is — which is what ``_current_tenant_for_session`` does on checkout.
    """

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def _require_tenant(self) -> None:
        if core_context._tenant_context.get() is None:
            raise TenantContextError(
                "Row-level security is enabled (DB_RLS_ENABLED=true) but no "
                "tenant is bound to this context."
            )
        self.seen.append(get_current_tenant_id())

    async def list_resumable(self, tenant_id=None, *, limit=None):
        self._require_tenant()
        return await super().list_resumable(tenant_id, limit=limit)

    async def load(self, run_id):
        self._require_tenant()
        return await super().load(run_id)

    async def save(self, checkpoint):
        self._require_tenant()
        return await super().save(checkpoint)


async def _seed(store, run_id: str, *, tenant_id: str | None, updated_at=None):
    checkpoint = Checkpoint(
        run_id=run_id,
        query="continue the work",
        status=STATUS_RUNNING,
        tenant_id=tenant_id,
    )
    # Seeding happens with a tenant bound; the sweep is what runs unbound.
    await store.save(checkpoint)
    if updated_at is not None:
        # The store deep-copies on save/load, so age the stored row directly.
        store._store[run_id]["updated_at"] = updated_at
    store.seen.clear()
    return checkpoint


class TestResumeSweep:
    async def test_an_unbound_sweep_still_finds_resumable_runs(self):
        store = _RlsCheckpointStore()
        await _seed(store, "r1", tenant_id="acme")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={"response": "ok"})

        with no_tenant():
            report = await resume_interrupted_runs(orchestrator, store)

        assert report.resumed == ["r1"]

    async def test_discovery_runs_as_the_system_tenant(self):
        store = _RlsCheckpointStore()
        await _seed(store, "r1", tenant_id="acme")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={"response": "ok"})

        with no_tenant():
            await resume_interrupted_runs(orchestrator, store)

        # list_resumable + load, both before any run's tenant is known.
        assert store.seen == [SYSTEM_TENANT_ID, SYSTEM_TENANT_ID]

    async def test_the_resume_still_runs_as_the_runs_own_tenant(self):
        """The system scope covers discovery only — re-entering a run under
        ``system`` would stamp the wrong owner on everything it touches."""
        store = _RlsCheckpointStore()
        await _seed(store, "r1", tenant_id="acme")
        during: list[str] = []
        orchestrator = AsyncMock()

        async def _process(*_args, **_kwargs):
            during.append(get_current_tenant_id())
            return {"response": "ok"}

        orchestrator.process = AsyncMock(side_effect=_process)

        with no_tenant():
            await resume_interrupted_runs(orchestrator, store)

        assert during == ["acme"]

    async def test_the_callers_context_is_restored(self):
        store = _RlsCheckpointStore()
        await _seed(store, "r1", tenant_id="acme")
        orchestrator = AsyncMock()
        orchestrator.process = AsyncMock(return_value={"response": "ok"})

        with no_tenant():
            await resume_interrupted_runs(orchestrator, store)
            assert core_context._tenant_context.get() is None


class TestStaleSweep:
    async def test_an_unbound_stale_sweep_still_fails_wedged_runs(self):
        store = _RlsCheckpointStore()
        await _seed(store, "r1", tenant_id="acme", updated_at=0.0)

        with no_tenant():
            report = await sweep_stale_runs(store, max_age_seconds=60.0, now=10_000.0)

        assert report.stale == ["r1"]
        assert report.checked == 1

    async def test_the_write_back_is_scoped_too(self):
        """The sweep reads *and writes* (it marks the run failed); the store
        scopes rows by column, so ``system`` never rewrites another tenant's
        ownership."""
        store = _RlsCheckpointStore()
        await _seed(store, "r1", tenant_id="acme", updated_at=0.0)

        with no_tenant():
            await sweep_stale_runs(store, max_age_seconds=60.0, now=10_000.0)

        assert set(store.seen) == {SYSTEM_TENANT_ID}
        saved = await store.load("r1")
        assert saved.status == STATUS_FAILED
        assert saved.tenant_id == "acme"
