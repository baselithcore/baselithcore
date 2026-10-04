"""The blocking bridge: dedicated loop, thread-safe, scoped, closable."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from qdrant_client.models import PointStruct, VectorParams

from core.config.inference import QdrantServerConfig
from core.services.inference import QdrantRuntime, SyncInference, TenantScopeError


@pytest.fixture
def bridge():  # type: ignore[no-untyped-def]
    b = SyncInference()
    b.use_qdrant_runtime(
        QdrantRuntime.open(QdrantServerConfig(url=":memory:"), allow_memory=True)
    )
    yield b
    b.close()


def test_sync_store_round_trip_and_isolation(bridge: SyncInference) -> None:
    a = bridge.store("wikigen", "ta")
    b = bridge.store("wikigen", "tb")
    a.create_collection("c", vectors_config=VectorParams(size=2, distance="Cosine"))  # type: ignore[arg-type]
    b.create_collection("c", vectors_config=VectorParams(size=2, distance="Cosine"))  # type: ignore[arg-type]
    a.upsert("c", [PointStruct(id=1, vector=[1.0, 0.0], payload={"w": "a"})])
    assert [p.payload["w"] for p in a.search("c", [1.0, 0.0])] == ["a"]
    assert b.search("c", [1.0, 0.0]) == []
    assert a.list_collections() == ["c"]


def test_concurrent_threads_share_one_loop(bridge: SyncInference) -> None:
    store = bridge.store("wikigen", "t")
    store.create_collection("c", vectors_config=VectorParams(size=2, distance="Cosine"))  # type: ignore[arg-type]

    def work(i: int) -> int:
        store.upsert("c", [PointStruct(id=i, vector=[1.0, float(i)], payload={})])
        return threading.get_ident()

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(work, range(1, 25)))
    assert store.count("c").count == 24
    assert sum(t.name == "baselith-inference" for t in threading.enumerate()) == 1


def test_unknown_method_is_not_exposed(bridge: SyncInference) -> None:
    store = bridge.store("wikigen", "t")
    with pytest.raises(AttributeError):
        store.create_snapshot


def test_empty_tenant_fails_closed(bridge: SyncInference) -> None:
    with pytest.raises(TenantScopeError):
        bridge.store("wikigen", "")


def test_close_is_idempotent_and_loop_restarts() -> None:
    b = SyncInference()
    b.use_qdrant_runtime(
        QdrantRuntime.open(QdrantServerConfig(url=":memory:"), allow_memory=True)
    )
    b.store("p", "t")
    b.close()
    b.close()
    assert not any(t.name == "baselith-inference" for t in threading.enumerate())


class _Closable:
    def __init__(self, fail: bool) -> None:
        self.fail = fail
        self.closed = False

    async def shutdown(self) -> None:
        self.closed = True
        if self.fail:
            raise RuntimeError("boom")


def test_one_failing_service_does_not_leave_the_others_open() -> None:
    b = SyncInference()
    b.run(_noop())  # start the loop
    broken, rerank, qdrant = _Closable(True), _Closable(False), _Closable(False)
    vars(b).update(_embedding=broken, _rerank=rerank, _qdrant=qdrant)
    b.close()
    assert broken.closed and rerank.closed and qdrant.closed
    assert not any(t.name == "baselith-inference" for t in threading.enumerate())


async def _noop() -> None:
    return None


async def test_async_shutdown_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.services.inference import sync_bridge

    def _boom() -> None:
        raise RuntimeError("bridge exploded")

    monkeypatch.setattr(sync_bridge, "shutdown_sync_inference", _boom)
    await sync_bridge.ashutdown_sync_inference()  # logged, not raised
