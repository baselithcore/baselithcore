"""Tenant isolation of the scoped vector store (in-memory Qdrant)."""

from __future__ import annotations

import pytest
from qdrant_client.models import PointStruct, VectorParams

from core.config.inference import QdrantServerConfig
from core.services.inference import (
    InferenceConfigError,
    QdrantRuntime,
    TenantScopeError,
)
from core.services.inference.scope import scoped_name

VEC = VectorParams(size=2, distance="Cosine")  # type: ignore[arg-type]


@pytest.fixture
async def runtime():  # type: ignore[no-untyped-def]
    rt = QdrantRuntime.open(QdrantServerConfig(url=":memory:"), allow_memory=True)
    yield rt
    await rt.shutdown()


async def test_tenant_a_cannot_see_or_write_tenant_b(runtime: QdrantRuntime) -> None:
    a = runtime.scoped("wikigen", "tenant-a")
    b = runtime.scoped("wikigen", "tenant-b")
    await a.create_collection("docs", vectors_config=VEC)
    await b.create_collection("docs", vectors_config=VEC)
    await a.upsert("docs", [PointStruct(id=1, vector=[1.0, 0.0], payload={"who": "a"})])
    await b.upsert("docs", [PointStruct(id=1, vector=[1.0, 0.0], payload={"who": "b"})])

    hits_a = await a.search("docs", [1.0, 0.0])
    hits_b = await b.search("docs", [1.0, 0.0])
    assert [h.payload["who"] for h in hits_a] == ["a"]
    assert [h.payload["who"] for h in hits_b] == ["b"]
    assert await a.list_collections() == ["docs"]

    await a.delete_collection("docs")
    assert not await a.collection_exists("docs")
    assert await b.collection_exists("docs")  # untouched


async def test_other_plugin_same_tenant_is_separate(runtime: QdrantRuntime) -> None:
    w = runtime.scoped("wikigen", "t")
    d = runtime.scoped("docheck", "t")
    await w.create_collection("docs", vectors_config=VEC)
    assert not await d.collection_exists("docs")
    assert await d.list_collections() == []


def test_names_cannot_escape_scope() -> None:
    for bad in ("../x", "a.b", "other.tenant.docs", "", "x" * 200, "a b"):
        with pytest.raises(TenantScopeError):
            scoped_name("t", "wikigen", bad)
    with pytest.raises(TenantScopeError):
        scoped_name("t", "wiki.gen", "docs")


def test_dot_in_tenant_cannot_forge_another_scope() -> None:
    forged = scoped_name("a.wikigen", "p", "docs")
    assert forged != scoped_name("a", "wikigen", "docs")
    assert forged.count(".") == 2  # tenant was hashed, no extra separators
    assert scoped_name("user@x.io", "p", "d") == scoped_name("user@x.io", "p", "d")
    assert scoped_name("user@x.io", "p", "d") != scoped_name("user@x.co", "p", "d")


@pytest.mark.parametrize("tenant", ["", "   "])
def test_empty_tenant_fails_closed(tenant: str) -> None:
    with pytest.raises(TenantScopeError):
        scoped_name(tenant, "wikigen", "docs")


def test_runtime_refuses_embedded_and_missing_url() -> None:
    with pytest.raises(InferenceConfigError, match="BASELITH_QDRANT_URL"):
        QdrantRuntime.open(QdrantServerConfig(url=None))
    with pytest.raises(InferenceConfigError, match="tests only"):
        QdrantRuntime.open(QdrantServerConfig(url=":memory:"))
    with pytest.raises(InferenceConfigError, match="http"):
        QdrantRuntime.open(QdrantServerConfig(url="/var/lib/qdrant"))
