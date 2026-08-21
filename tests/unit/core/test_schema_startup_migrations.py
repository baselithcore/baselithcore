"""init_db honors DB_MIGRATIONS_ON_STARTUP (pre-deploy migration Job mode)."""

from unittest.mock import AsyncMock, patch

import core.db.schema as schema_module


async def test_init_db_runs_migrations_by_default(monkeypatch):
    monkeypatch.setattr(schema_module, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(schema_module._storage_config, "db_migrations_on_startup", True)
    with patch.object(schema_module, "ensure_schema", new=AsyncMock()) as ens:
        await schema_module.init_db()
    ens.assert_awaited_once()


async def test_init_db_skips_migrations_when_disabled(monkeypatch):
    monkeypatch.setattr(schema_module, "POSTGRES_ENABLED", True)
    monkeypatch.setattr(
        schema_module._storage_config, "db_migrations_on_startup", False
    )
    with patch.object(schema_module, "ensure_schema", new=AsyncMock()) as ens:
        await schema_module.init_db()
    ens.assert_not_awaited()
