"""HNSW tuning and the boot-time dimension assertion for the pgvector provider.

``CREATE TABLE IF NOT EXISTS`` is silent when the table already exists with a
*different* vector width, so a changed ``VECTORSTORE_EMBEDDING_DIM`` used to
surface as a per-row insert error much later. And an untuned HNSW index plus a
server-default ``ef_search`` means recall nobody chose.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config.vectorstore import VectorStoreConfig
from core.services.vectorstore.exceptions import VectorStoreError
from core.services.vectorstore.providers.pgvector_provider import PgVectorProvider

pytestmark = [pytest.mark.unit]


def _cursor(dimension: int | None = None):
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.executemany = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(
        return_value=None if dimension is None else (dimension,)
    )
    return cursor


def _patched(cursor):
    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield cursor

    return patch(
        "core.services.vectorstore.providers.pgvector_provider.get_async_cursor",
        _ctx,
    )


def _config(**kwargs):
    return patch(
        "core.services.vectorstore.providers.pgvector_provider.get_vectorstore_config",
        return_value=VectorStoreConfig(**kwargs),
    )


def _sqls(cursor):
    return [call.args[0] for call in cursor.execute.call_args_list]


# --------------------------------------------------------------------------- #
# Index build parameters
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestHnswBuildParameters:
    async def test_defaults_match_pgvector(self):
        cursor = _cursor()
        with _patched(cursor), _config():
            await PgVectorProvider().create_collection("documents", 384)
        joined = "\n".join(_sqls(cursor))
        assert "USING hnsw (embedding vector_cosine_ops)" in joined
        assert "m = 16" in joined
        assert "ef_construction = 64" in joined

    async def test_configured_values_reach_the_ddl(self):
        cursor = _cursor()
        with (
            _patched(cursor),
            _config(hnsw_m=32, hnsw_ef_construction=200),
        ):
            await PgVectorProvider().create_collection("documents", 384)
        joined = "\n".join(_sqls(cursor))
        assert "m = 32" in joined
        assert "ef_construction = 200" in joined

    async def test_config_rejects_ef_construction_below_twice_m(self):
        with pytest.raises(ValueError, match="ef_construction"):
            VectorStoreConfig(hnsw_m=32, hnsw_ef_construction=16)


# --------------------------------------------------------------------------- #
# Dimension assertion
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestDimensionAssertion:
    async def test_absent_table_proceeds(self):
        cursor = _cursor(dimension=None)
        with _patched(cursor), _config():
            await PgVectorProvider().create_collection("documents", 384)
        assert any("CREATE TABLE IF NOT EXISTS" in sql for sql in _sqls(cursor))

    async def test_matching_dimension_proceeds(self):
        cursor = _cursor(dimension=384)
        with _patched(cursor), _config():
            await PgVectorProvider().create_collection("documents", 384)
        assert any("CREATE TABLE IF NOT EXISTS" in sql for sql in _sqls(cursor))

    async def test_mismatched_dimension_raises_before_any_ddl(self):
        cursor = _cursor(dimension=768)
        with _patched(cursor), _config():
            with pytest.raises(VectorStoreError, match="768"):
                await PgVectorProvider().create_collection("documents", 384)
        assert not any("CREATE TABLE IF NOT EXISTS" in sql for sql in _sqls(cursor))

    async def test_error_names_both_dimensions_and_the_table(self):
        cursor = _cursor(dimension=768)
        with _patched(cursor), _config():
            with pytest.raises(VectorStoreError) as excinfo:
                await PgVectorProvider().create_collection("documents", 384)
        message = str(excinfo.value)
        assert "vs_documents" in message and "384" in message and "768" in message

    async def test_unknown_typmod_is_not_treated_as_a_mismatch(self):
        """An unconstrained ``vector`` column reports ``-1``; not a conflict."""
        cursor = _cursor(dimension=-1)
        with _patched(cursor), _config():
            await PgVectorProvider().create_collection("documents", 384)
        assert any("CREATE TABLE IF NOT EXISTS" in sql for sql in _sqls(cursor))


# --------------------------------------------------------------------------- #
# Per-query ef_search
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
class TestEfSearchPerQuery:
    async def test_search_sets_ef_search_in_a_transaction(self):
        cursor = _cursor()
        with _patched(cursor), _config(hnsw_ef_search=120):
            await PgVectorProvider().search("documents", [0.1, 0.2], limit=5)
        statements = _sqls(cursor)
        assert statements[0] == "SET LOCAL hnsw.ef_search = 120"
        assert "<=>" in statements[-1]
        cursor.connection.transaction.assert_called_once()

    async def test_default_ef_search_is_pgvectors(self):
        cursor = _cursor()
        with _patched(cursor), _config():
            await PgVectorProvider().search("documents", [0.1])
        assert _sqls(cursor)[0] == "SET LOCAL hnsw.ef_search = 40"

    async def test_ef_search_value_is_always_an_integer(self):
        """The value is interpolated (``SET`` takes no bind parameters), so it
        must never be able to carry anything but a number."""
        cursor = _cursor()
        with _patched(cursor), _config(hnsw_ef_search=64):
            await PgVectorProvider().search("documents", [0.1])
        statement = _sqls(cursor)[0]
        assert statement.rsplit("=", 1)[1].strip().isdigit()

    async def test_zero_disables_the_per_query_override(self):
        cursor = _cursor()
        with _patched(cursor), _config(hnsw_ef_search=0):
            await PgVectorProvider().search("documents", [0.1])
        assert not any("ef_search" in sql for sql in _sqls(cursor))
        cursor.connection.transaction.assert_not_called()

    async def test_results_are_still_returned(self):
        cursor = _cursor()
        cursor.fetchall = AsyncMock(
            return_value=[{"id": "a", "score": 0.9, "payload": {}}]
        )
        with _patched(cursor), _config(hnsw_ef_search=100):
            hits = await PgVectorProvider().search("documents", [0.1])
        assert [hit.id for hit in hits] == ["a"]

    async def test_other_operations_do_not_open_a_transaction(self):
        cursor = _cursor()
        with _patched(cursor), _config():
            await PgVectorProvider().retrieve("documents", ["p1"])
            await PgVectorProvider().delete("documents", ["p1"])
        cursor.connection.transaction.assert_not_called()
