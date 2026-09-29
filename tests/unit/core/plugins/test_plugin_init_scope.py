"""Plugin activation runs as the ``system`` tenant when RLS is on.

With ``DB_RLS_ENABLED=true`` a database checkout with no tenant bound raises,
so a plugin that touched its store from ``initialize()`` failed to activate and
a scheduler it started failed every tick. Activation is boot work: it runs
inside ``system_tenant_scope()``, and loops it spawns inherit that identity.
"""

from __future__ import annotations

import asyncio

import pytest

from core.context import get_current_tenant_id, tenant_is_bound
from core.db import connection
from core.db.session_setup import SYSTEM_TENANT_ID
from core.plugins.init_scope import plugin_init_scope


def test_rls_off_binds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection, "DB_RLS_ENABLED", False)
    before = (tenant_is_bound(), get_current_tenant_id())
    with plugin_init_scope():
        assert (tenant_is_bound(), get_current_tenant_id()) == before


def test_rls_on_binds_the_system_tenant_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection, "DB_RLS_ENABLED", True)
    before = get_current_tenant_id()
    with plugin_init_scope():
        assert get_current_tenant_id() == SYSTEM_TENANT_ID
    assert get_current_tenant_id() == before


async def test_a_loop_started_during_activation_keeps_the_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connection, "DB_RLS_ENABLED", True)
    seen: list[str] = []

    async def tick() -> None:
        await asyncio.sleep(0)
        seen.append(get_current_tenant_id())

    with plugin_init_scope():
        task = asyncio.create_task(tick())
    await task
    assert seen == [SYSTEM_TENANT_ID]
