"""Cross-replica prompt persistence: write-through backend + refresh loop.

The in-memory registry made runtime label promotion replica-local. The
synchronizer closes that: writes go through a durable backend AND the local
registry; every replica's periodic refresh imports versions/labels written
elsewhere. The backend here is a fake — the Postgres implementation is a thin
SQL layer sharing this contract.
"""

from __future__ import annotations

import asyncio

import pytest

from core.prompts.registry import PromptRegistry
from core.prompts.sync import PromptSynchronizer
from core.prompts.types import PromptVersion


class FakeBackend:
    """In-memory durable backend implementing the synchronizer's contract."""

    def __init__(self) -> None:
        self.versions: dict[tuple[str, str], PromptVersion] = {}
        self.labels: dict[tuple[str, str], str] = {}
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def upsert_version(self, version: PromptVersion) -> None:
        self.versions[(version.name, version.version)] = version

    async def set_label(self, name: str, label: str, version: str) -> None:
        self.labels[(name, label)] = version

    async def fetch_all(
        self,
    ) -> tuple[list[PromptVersion], dict[tuple[str, str], str]]:
        return list(self.versions.values()), dict(self.labels)


def _pv(name: str, version: str, template: str = "t {{ x }}") -> PromptVersion:
    return PromptVersion(name=name, version=version, template=template)


@pytest.mark.asyncio
async def test_push_version_writes_backend_and_local_registry():
    backend = FakeBackend()
    registry = PromptRegistry()
    syncer = PromptSynchronizer(registry=registry, backend=backend)

    await syncer.push_version(_pv("greeting", "1"))

    assert ("greeting", "1") in backend.versions
    assert registry.get("greeting", version="1").template == "t {{ x }}"


@pytest.mark.asyncio
async def test_push_label_promotes_in_backend_and_locally():
    backend = FakeBackend()
    registry = PromptRegistry()
    syncer = PromptSynchronizer(registry=registry, backend=backend)
    await syncer.push_version(_pv("greeting", "1"))
    await syncer.push_version(_pv("greeting", "2", "new {{ x }}"))

    await syncer.push_label("greeting", "production", "2")

    assert backend.labels[("greeting", "production")] == "2"
    assert registry.get("greeting", label="production").version == "2"


@pytest.mark.asyncio
async def test_refresh_imports_remote_writes_into_local_registry():
    backend = FakeBackend()
    # Replica A wrote through its own synchronizer.
    writer = PromptSynchronizer(registry=PromptRegistry(), backend=backend)
    await writer.push_version(_pv("greeting", "1"))
    await writer.push_version(_pv("greeting", "2", "new {{ x }}"))
    await writer.push_label("greeting", "production", "2")

    # Replica B only refreshes.
    replica_registry = PromptRegistry()
    reader = PromptSynchronizer(registry=replica_registry, backend=backend)
    await reader.refresh()

    assert replica_registry.get("greeting", label="production").version == "2"
    assert len(replica_registry.list_versions("greeting")) == 2


@pytest.mark.asyncio
async def test_refresh_is_idempotent():
    backend = FakeBackend()
    registry = PromptRegistry()
    syncer = PromptSynchronizer(registry=registry, backend=backend)
    await syncer.push_version(_pv("greeting", "1"))

    await syncer.refresh()
    await syncer.refresh()

    assert len(registry.list_versions("greeting")) == 1


@pytest.mark.asyncio
async def test_refresh_survives_backend_errors():
    class BrokenBackend(FakeBackend):
        async def fetch_all(self):
            raise ConnectionError("db down")

    syncer = PromptSynchronizer(registry=PromptRegistry(), backend=BrokenBackend())
    # Fail-open: a refresh error must not propagate (the loop keeps running).
    await syncer.refresh()


@pytest.mark.asyncio
async def test_background_loop_refreshes_periodically():
    backend = FakeBackend()
    registry = PromptRegistry()
    syncer = PromptSynchronizer(
        registry=registry, backend=backend, interval_seconds=0.05
    )
    await syncer.start()
    try:
        await PromptSynchronizer(
            registry=PromptRegistry(), backend=backend
        ).push_version(_pv("late", "1"))
        for _ in range(40):
            if registry.list_versions("late"):
                break
            await asyncio.sleep(0.05)
        assert registry.list_versions("late"), "loop never imported the remote write"
    finally:
        await syncer.stop()


@pytest.mark.asyncio
async def test_refresh_skips_a_bad_row_and_imports_the_rest():
    backend = FakeBackend()
    writer = PromptSynchronizer(registry=PromptRegistry(), backend=backend)
    await writer.push_version(_pv("good", "1"))
    await writer.push_version(_pv("bad", "1"))
    await writer.push_label("good", "production", "1")
    # A label pointing at a version this replica never received.
    backend.labels[("ghost", "production")] = "9"

    registry = PromptRegistry()
    real_put = registry.store.put

    def flaky_put(version: PromptVersion) -> None:
        if version.name == "bad":
            raise ValueError("corrupt row")
        real_put(version)

    registry.store.put = flaky_put  # type: ignore[method-assign]
    reader = PromptSynchronizer(registry=registry, backend=backend)

    await reader.refresh()

    assert registry.get("good", label="production").version == "1"


@pytest.mark.asyncio
async def test_the_loop_survives_a_failing_tick():
    backend = FakeBackend()
    syncer = PromptSynchronizer(
        registry=PromptRegistry(), backend=backend, interval_seconds=0.01
    )
    calls = {"n": 0}
    real_refresh = syncer.refresh

    async def boom_once() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("unexpected")
        await real_refresh()

    syncer.refresh = boom_once  # type: ignore[method-assign]
    task = asyncio.get_running_loop().create_task(syncer._loop())
    try:
        for _ in range(100):
            if calls["n"] >= 3:
                break
            await asyncio.sleep(0.01)
        assert calls["n"] >= 3
        assert not task.done()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
