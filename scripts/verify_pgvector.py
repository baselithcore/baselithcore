#!/usr/bin/env python
"""End-to-end verification of the pgvector vector store backend.

Runs a real roundtrip (create collection → upsert → search with tenant
isolation and filters → scroll → delete) against the configured PostgreSQL
(``DATABASE_URL`` or ``DB_HOST``/``DB_PORT``/... env vars). The database must
have the ``vector`` extension available — the docker compose Postgres image
(``pgvector/pgvector:pg16``) ships it.

The pytest suite mocks psycopg globally (``tests/conftest.py``), so this
lives as a standalone script rather than a test module:

    docker compose up -d postgres
    DATABASE_URL=postgresql://baselith:baselith@localhost:5432/baselith \
        python scripts/verify_pgvector.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid


async def main() -> int:
    from core.db.connection import get_async_cursor
    from core.services.vectorstore.providers.pgvector_provider import PgVectorProvider

    provider = PgVectorProvider()
    collection = f"verify_{uuid.uuid4().hex[:10]}"
    table = f"vs_{collection}"

    await provider.create_collection(collection, vector_size=3)
    print(f"[1/6] collection created ({table})")

    await provider.upsert(
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
    print("[2/6] upserted 3 points (2 tenants)")

    hits = await provider.search(collection, [1.0, 0.0, 0.0], limit=10, tenant_id="t1")
    ids = [h.id for h in hits]
    assert ids and ids[0] == "a", f"expected best hit 'a', got {ids}"
    assert "c" not in ids, "tenant isolation violated: cross-tenant hit returned"
    assert abs((hits[0].score or 0) - 1.0) < 1e-6, hits[0].score
    print(
        f"[3/6] tenant-isolated search OK (hits={ids}, top score={hits[0].score:.4f})"
    )

    filtered = await provider.search(
        collection, [1.0, 0.0, 0.0], filter={"text": "beta"}
    )
    assert [h.id for h in filtered] == ["b"], filtered
    print("[4/6] dict payload filter OK")

    page1, offset = await provider.scroll(collection, limit=2)
    assert len(page1) == 2 and offset is not None
    page2, _ = await provider.scroll(collection, limit=2, offset=offset)
    assert len(page2) == 1
    print("[5/6] keyset scroll OK")

    await provider.upsert(
        collection,
        [{"id": "a", "vector": [0.0, 1.0, 0.0], "payload": {"tenant_id": "t1"}}],
    )
    assert len(await provider.retrieve(collection, ["a"])) == 1  # update, no dup
    await provider.delete_by_filter(collection, "text", "beta")
    remaining, _ = await provider.scroll(collection, limit=10)
    assert sorted(p.id for p in remaining) == ["a", "c"], remaining
    print("[6/6] upsert-update + delete_by_filter OK")

    async with get_async_cursor() as cur:
        await cur.execute(f"DROP TABLE IF EXISTS {table}")
    print("PASS: pgvector backend verified end-to-end")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
