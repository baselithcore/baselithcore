"""Integration test: PgVectorProvider against a real PostgreSQL + pgvector.

Runs only when Postgres is reachable (docker compose up -d postgres) and the
``vector`` extension is installable — skipped otherwise, so the unit CI job
is unaffected. The compose Postgres image is ``pgvector/pgvector:pg16``,
which ships the extension.
"""

import uuid

import pytest

from core.services.vectorstore.exceptions import VectorStoreError
from core.services.vectorstore.providers.pgvector_provider import PgVectorProvider

pytestmark = [pytest.mark.integration]


async def _pg_available() -> bool:
    """True only against a REAL PostgreSQL.

    ``tests/conftest.py`` mocks psycopg globally, and a MagicMock cursor
    happily "executes" anything — so require the actual value of
    ``SELECT 1`` to come back, which no mock produces.
    """
    try:
        from core.db.connection import get_async_cursor

        async with get_async_cursor() as cur:
            await cur.execute("SELECT 1")
            row = await cur.fetchone()
        value = row[0] if isinstance(row, (tuple, list)) else None
        return value == 1
    except Exception:
        return False


@pytest.fixture
async def provider():
    if not await _pg_available():
        pytest.skip("PostgreSQL not reachable (docker compose up -d postgres)")
    provider = PgVectorProvider()
    collection = f"it_{uuid.uuid4().hex[:10]}"
    try:
        await provider.create_collection(collection, vector_size=3)
    except VectorStoreError as exc:
        pytest.skip(f"pgvector extension unavailable: {exc}")
    yield provider, collection
    from core.db.connection import get_async_cursor

    async with get_async_cursor() as cur:
        await cur.execute(f"DROP TABLE IF EXISTS vs_{collection}")


@pytest.mark.asyncio
class TestPgVectorRoundTrip:
    async def test_upsert_search_roundtrip_with_tenant_isolation(self, provider):
        pg, collection = provider
        await pg.upsert(
            collection,
            [
                {
                    "id": "a",
                    "vector": [1.0, 0.0, 0.0],
                    "payload": {"tenant_id": "t1", "text": "alpha"},
                },
                {
                    "id": "b",
                    "vector": [0.9, 0.1, 0.0],
                    "payload": {"tenant_id": "t1", "text": "beta"},
                },
                {
                    "id": "c",
                    "vector": [1.0, 0.0, 0.0],
                    "payload": {"tenant_id": "t2", "text": "other-tenant"},
                },
            ],
        )
        hits = await pg.search(collection, [1.0, 0.0, 0.0], limit=10, tenant_id="t1")
        ids = [h.id for h in hits]
        assert ids[0] == "a"  # exact match ranks first (cosine sim ~1.0)
        assert "c" not in ids  # tenant isolation enforced
        assert hits[0].score == pytest.approx(1.0, abs=1e-6)
        assert hits[0].payload["text"] == "alpha"

    async def test_filter_scroll_delete(self, provider):
        pg, collection = provider
        await pg.upsert(
            collection,
            [
                {"id": f"p{i}", "vector": [0.0, 1.0, 0.0], "payload": {"doc": "d1"}}
                for i in range(3)
            ]
            + [{"id": "q1", "vector": [0.0, 1.0, 0.0], "payload": {"doc": "d2"}}],
        )
        # dict filter on search
        hits = await pg.search(collection, [0.0, 1.0, 0.0], filter={"doc": "d2"})
        assert [h.id for h in hits] == ["q1"]
        # keyset scroll
        page1, offset = await pg.scroll(collection, limit=2)
        assert len(page1) == 2 and offset is not None
        page2, offset2 = await pg.scroll(collection, limit=2, offset=offset)
        assert len(page2) == 2
        # upsert twice = update, not duplicate
        await pg.upsert(
            collection,
            [{"id": "q1", "vector": [0.0, 0.0, 1.0], "payload": {"doc": "d2"}}],
        )
        again = await pg.retrieve(collection, ["q1"])
        assert len(again) == 1
        # delete_by_filter removes only the matching doc
        await pg.delete_by_filter(collection, "doc", "d1")
        remaining, _ = await pg.scroll(collection, limit=10)
        assert [p.id for p in remaining] == ["q1"]
