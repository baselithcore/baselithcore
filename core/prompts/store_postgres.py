"""Postgres backend for durable prompt versions and labels.

Thin SQL layer implementing the :class:`~core.prompts.sync.PromptBackend`
contract; all synchronization semantics live in ``core.prompts.sync``. The
schema is created idempotently on :meth:`initialize` (mirroring the
checkpoint store's approach).
"""

from __future__ import annotations

import json
from typing import Any

from core.db.connection import get_async_cursor, system_tenant_scope
from core.db.ddl import skip_runtime_ddl
from core.observability.logging import get_logger
from core.prompts.types import PromptVersion

logger = get_logger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS prompt_versions (
    name        TEXT NOT NULL,
    version     TEXT NOT NULL,
    template    TEXT NOT NULL,
    description TEXT,
    variables   JSONB NOT NULL DEFAULT '[]',
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (name, version)
);
CREATE TABLE IF NOT EXISTS prompt_labels (
    name    TEXT NOT NULL,
    label   TEXT NOT NULL,
    version TEXT NOT NULL,
    PRIMARY KEY (name, label)
);
"""

_UPSERT_VERSION = """
INSERT INTO prompt_versions (
    name, version, template, description, variables, metadata, created_at
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (name, version) DO UPDATE SET
    template = EXCLUDED.template,
    description = EXCLUDED.description,
    variables = EXCLUDED.variables,
    metadata = EXCLUDED.metadata
"""

_UPSERT_LABEL = """
INSERT INTO prompt_labels (name, label, version) VALUES (%s, %s, %s)
ON CONFLICT (name, label) DO UPDATE SET version = EXCLUDED.version
"""

_SELECT_VERSIONS = (
    "SELECT name, version, template, description, variables, metadata, created_at "
    "FROM prompt_versions ORDER BY created_at"
)
_SELECT_LABELS = "SELECT name, label, version FROM prompt_labels"


class PostgresPromptBackend:
    """Durable prompt storage in PostgreSQL.

    Every method runs inside :func:`core.db.connection.system_tenant_scope`.
    Prompts are **deployment-global**: neither table carries a ``tenant_id``
    column and neither is covered by a row-level-security policy, so there is no
    tenant this store could be attributed to. What there *is*, under
    ``DB_RLS_ENABLED``, is a pool that refuses to check out a connection for a
    caller that bound no tenant — and the callers here are out of request by
    construction: the schema bootstrap, and ``PromptSynchronizer.refresh``,
    which runs on a background timer. Unscoped, ``refresh`` raised
    ``TenantContextError`` into its own fail-open handler on every tick, so
    prompt sync logged a warning and quietly never synced.
    """

    async def initialize(self) -> None:
        """Create the prompt tables if absent (idempotent)."""
        if skip_runtime_ddl("prompt store", "prompt_versions, prompt_labels"):
            return
        with system_tenant_scope():
            async with get_async_cursor() as cur:
                await cur.execute(_DDL)
        logger.info("prompt_store_schema_initialized")

    async def upsert_version(self, version: PromptVersion) -> None:
        """Insert or update one prompt version."""
        with system_tenant_scope():
            async with get_async_cursor() as cur:
                await cur.execute(
                    _UPSERT_VERSION,
                    (
                        version.name,
                        version.version,
                        version.template,
                        version.description,
                        json.dumps(version.variables),
                        json.dumps(version.metadata, default=str),
                        version.created_at,
                    ),
                )

    async def set_label(self, name: str, label: str, version: str) -> None:
        """Point a label (``prod``, ``canary``, …) at a version."""
        with system_tenant_scope():
            async with get_async_cursor() as cur:
                await cur.execute(_UPSERT_LABEL, (name, label, version))

    async def fetch_all(
        self,
    ) -> tuple[list[PromptVersion], dict[tuple[str, str], str]]:
        """Every stored version and label — the synchronizer's refresh read."""
        with system_tenant_scope():
            async with get_async_cursor() as cur:
                await cur.execute(_SELECT_VERSIONS)
                version_rows: list[tuple[Any, ...]] = await cur.fetchall()
                await cur.execute(_SELECT_LABELS)
                label_rows: list[tuple[Any, ...]] = await cur.fetchall()

        versions = [
            PromptVersion(
                name=row[0],
                version=row[1],
                template=row[2],
                description=row[3],
                variables=list(row[4] or []),
                metadata=dict(row[5] or {}),
                created_at=float(row[6]),
            )
            for row in version_rows
        ]
        labels = {(row[0], row[1]): row[2] for row in label_rows}
        return versions, labels


__all__ = ["PostgresPromptBackend"]
