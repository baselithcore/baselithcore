"""Integration test: QdrantProvider against a real Qdrant server.

Qdrant is the default vector backend, and every other test reaches it through
an embedded ``:memory:`` client or a mock — neither speaks the REST protocol,
applies server-side payload indexes or enforces the server's id rules. This
module runs the provider against the server image the compose stack and the CI
``integration_test`` job use.

Opt-in like the Postgres and Redis suites::

    docker compose up -d qdrant
    BASELITH_TEST_REAL_QDRANT=1 python -m pytest tests/integration/test_qdrant_integration.py

Host and port come from ``VECTORSTORE_HOST`` / ``VECTORSTORE_PORT``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

pytestmark = [pytest.mark.integration]

A, B, C = (str(uuid.UUID(int=n)) for n in (1, 2, 3))


def _enabled() -> bool:
    return os.environ.get("BASELITH_TEST_REAL_QDRANT", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


@pytest.fixture
async def qdrant() -> AsyncIterator[tuple[Any, str]]:
    if not _enabled():
        pytest.skip("set BASELITH_TEST_REAL_QDRANT=1 (and run Qdrant) to enable")
    pytest.importorskip("qdrant_client")
    from core.config import get_vectorstore_config
    from core.services.vectorstore.providers.qdrant_provider import QdrantProvider

    config = get_vectorstore_config()
    provider = QdrantProvider(host=config.host, port=config.port, timeout=10)
    try:
        await provider.client.get_collections()
    except Exception as exc:
        pytest.skip(f"Qdrant not reachable at {config.host}:{config.port}: {exc}")
    collection = f"it_{uuid.uuid4().hex[:10]}"
    await provider.create_collection(collection, vector_size=3)
    try:
        yield provider, collection
    finally:
        await provider.client.delete_collection(collection)
        await provider.client.close()


async def test_upsert_search_with_tenant_isolation(qdrant: tuple[Any, str]) -> None:
    provider, collection = qdrant
    await provider.upsert(
        collection,
        [
            {
                "id": A,
                "vector": [1.0, 0.0, 0.0],
                "payload": {"tenant_id": "t1", "text": "alpha"},
            },
            {
                "id": B,
                "vector": [0.9, 0.1, 0.0],
                "payload": {"tenant_id": "t1", "text": "beta"},
            },
            {
                "id": C,
                "vector": [1.0, 0.0, 0.0],
                "payload": {"tenant_id": "t2", "text": "other"},
            },
        ],
    )
    hits = await provider.search(collection, [1.0, 0.0, 0.0], limit=10, tenant_id="t1")
    ids = [str(hit.id) for hit in hits]
    assert ids[0] == A
    assert C not in ids
    assert hits[0].payload["text"] == "alpha"


async def test_create_is_idempotent_and_delete_by_filter_is_scoped(
    qdrant: tuple[Any, str],
) -> None:
    provider, collection = qdrant
    # A second create on the same width is the restart path: no error, no drop.
    await provider.create_collection(collection, vector_size=3)
    assert await provider.collection_exists(collection)

    await provider.upsert(
        collection,
        [
            {"id": A, "vector": [0.0, 1.0, 0.0], "payload": {"doc": "d1"}},
            {"id": B, "vector": [0.0, 1.0, 0.0], "payload": {"doc": "d1"}},
            {"id": C, "vector": [0.0, 1.0, 0.0], "payload": {"doc": "d2"}},
        ],
    )
    await provider.delete_by_filter(collection, "doc", "d1")
    points, _ = await provider.scroll(collection, limit=10)
    assert [str(p.id) for p in points] == [C]
    assert len(await provider.retrieve(collection, [C])) == 1
