"""Unit tests for the PostgreSQL + pgvector vector store provider."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.vectorstore.exceptions import VectorStoreError
from core.services.vectorstore.providers.pgvector_provider import (
    PgVectorPoint,
    PgVectorProvider,
)

pytestmark = [pytest.mark.unit]


def _cursor():
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[])
    cursor.fetchone = AsyncMock(return_value=None)
    return cursor


def _patched(cursor):
    @asynccontextmanager
    async def _ctx(*args, **kwargs):
        yield cursor

    return patch(
        "core.services.vectorstore.providers.pgvector_provider.get_async_cursor",
        _ctx,
    )


def _sqls(cursor):
    return [c.args[0] for c in cursor.execute.call_args_list]


@pytest.mark.asyncio
class TestCreateCollection:
    async def test_ddl_creates_extension_table_index(self):
        cursor = _cursor()
        with _patched(cursor):
            await PgVectorProvider().create_collection("documents", 384)
        joined = "\n".join(_sqls(cursor))
        assert "CREATE EXTENSION IF NOT EXISTS vector" in joined
        assert "CREATE TABLE IF NOT EXISTS vs_documents" in joined
        assert "vector(384)" in joined
        assert "hnsw" in joined and "vector_cosine_ops" in joined

    async def test_invalid_collection_name_rejected(self):
        with pytest.raises(VectorStoreError, match="collection"):
            await PgVectorProvider().create_collection("bad; DROP TABLE x", 8)


@pytest.mark.asyncio
class TestUpsert:
    async def test_upsert_conflict_update(self):
        cursor = _cursor()
        with _patched(cursor):
            await PgVectorProvider().upsert(
                "documents",
                [
                    {"id": "p1", "vector": [0.1, 0.2], "payload": {"k": "v"}},
                    {"id": "p2", "vector": [0.3, 0.4]},
                ],
            )
        joined = "\n".join(_sqls(cursor))
        assert "INSERT INTO vs_documents" in joined
        assert "ON CONFLICT (id) DO UPDATE" in joined
        first_params = cursor.execute.call_args_list[0].args[1]
        assert first_params[0] == "p1"
        assert first_params[1] == "[0.1,0.2]"  # pgvector text encoding


@pytest.mark.asyncio
class TestSearch:
    async def test_search_orders_by_cosine_distance(self):
        cursor = _cursor()
        cursor.fetchall = AsyncMock(
            return_value=[
                {"id": "a", "score": 0.9, "payload": {"text": "hello"}},
            ]
        )
        with _patched(cursor):
            hits = await PgVectorProvider().search("documents", [0.1, 0.2], limit=5)
        sql = _sqls(cursor)[0]
        assert "<=>" in sql and "ORDER BY" in sql and "LIMIT" in sql
        assert isinstance(hits[0], PgVectorPoint)
        assert hits[0].id == "a" and hits[0].score == 0.9
        assert hits[0].payload["text"] == "hello"

    async def test_search_score_threshold_and_filter(self):
        cursor = _cursor()
        with _patched(cursor):
            await PgVectorProvider().search(
                "documents",
                [0.1],
                limit=3,
                score_threshold=0.7,
                filter={"tenant_id": "t1"},
            )
        sql = _sqls(cursor)[0]
        assert "payload @>" in sql
        params = cursor.execute.call_args_list[0].args[1]
        assert 0.7 in params
        assert any("t1" in str(p) for p in params)


@pytest.mark.asyncio
class TestRetrieveDeleteScroll:
    async def test_retrieve_by_ids(self):
        cursor = _cursor()
        cursor.fetchall = AsyncMock(
            return_value=[{"id": "p1", "payload": {}, "embedding": "[0.1,0.2]"}]
        )
        with _patched(cursor):
            points = await PgVectorProvider().retrieve("documents", ["p1"])
        assert points[0].id == "p1"
        assert "WHERE id = ANY(%s)" in _sqls(cursor)[0]

    async def test_delete_by_ids(self):
        cursor = _cursor()
        with _patched(cursor):
            await PgVectorProvider().delete("documents", ["p1", "p2"])
        assert "DELETE FROM vs_documents" in _sqls(cursor)[0]

    async def test_scroll_keyset_pagination(self):
        cursor = _cursor()
        cursor.fetchall = AsyncMock(
            return_value=[{"id": f"p{i}", "payload": {}} for i in range(2)]
        )
        with _patched(cursor):
            points, next_offset = await PgVectorProvider().scroll(
                "documents", limit=2, offset="p0"
            )
        sql = _sqls(cursor)[0]
        assert "id > %s" in sql and "ORDER BY id" in sql
        assert len(points) == 2
        assert next_offset == "p1"  # page full → cursor to continue

    async def test_scroll_last_page_no_offset(self):
        cursor = _cursor()
        cursor.fetchall = AsyncMock(return_value=[{"id": "p9", "payload": {}}])
        with _patched(cursor):
            points, next_offset = await PgVectorProvider().scroll("documents", limit=5)
        assert next_offset is None

    async def test_delete_by_filter(self):
        cursor = _cursor()
        with _patched(cursor):
            await PgVectorProvider().delete_by_filter(
                "documents", "document_id", "doc-1"
            )
        sql = _sqls(cursor)[0]
        assert "DELETE FROM vs_documents" in sql
        assert "payload->>%s" in sql


class TestServiceWiring:
    def test_service_instantiates_pgvector_provider(self):
        from unittest.mock import MagicMock

        from core.services.vectorstore.providers.pgvector_provider import (
            PgVectorProvider,
        )
        from core.services.vectorstore.service import VectorStoreService

        config = MagicMock()
        config.provider = "pgvector"
        config.collection_name = "documents"
        service = VectorStoreService(config=config)
        assert isinstance(service.provider, PgVectorProvider)

    def test_config_accepts_pgvector(self):
        from core.config.services import VectorStoreConfig

        cfg = VectorStoreConfig(provider="pgvector")
        assert cfg.provider == "pgvector"
