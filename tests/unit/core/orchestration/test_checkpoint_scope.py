"""Child scopes keep composite runners' steps apart; writes never race the CAS."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from baselith.core.orchestration.checkpoint import (
    Checkpoint,
    CheckpointManager,
    InMemoryCheckpointStore,
)
from baselith.core.orchestration.checkpoint_scope import (
    bind_checkpoint,
    current_checkpoint,
    durable_step,
    resolve_checkpoint,
)

pytestmark = [pytest.mark.unit]


class _NetworkedStore(InMemoryCheckpointStore):
    """Yields between the version check and the write, like a database round trip."""

    async def save_step(self, checkpoint: Checkpoint, *args: Any) -> None:
        expected = checkpoint.version
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if checkpoint.version != expected:  # pragma: no cover - the race itself
            raise AssertionError("another save interleaved with this one")
        await super().save_step(checkpoint, *args)


async def _manager(
    store: InMemoryCheckpointStore, run_id: str = "r"
) -> CheckpointManager:
    checkpoint = Checkpoint(run_id=run_id)
    await store.save(checkpoint)
    return CheckpointManager(store, checkpoint)


async def test_concurrent_steps_are_serialised_and_all_persist() -> None:
    store = _NetworkedStore()
    manager = await _manager(store)

    async def work(i: int) -> int:
        await asyncio.sleep(0)
        return i

    results = await asyncio.gather(
        *(manager.run_step("unit", {"i": i}, lambda i=i: work(i)) for i in range(8))
    )
    assert results == list(range(8))
    stored = await store.load("r")
    assert stored is not None and len(stored.steps) == 8


async def test_sibling_scopes_never_share_a_record() -> None:
    store = InMemoryCheckpointStore()
    manager = await _manager(store)
    calls: list[str] = []

    async def call(tag: str) -> str:
        calls.append(tag)
        return tag

    one, two = manager.scope("task", {"i": 1}), manager.scope("task", {"i": 2})
    assert await one.run_step("search", {"q": "x"}, lambda: call("one")) == "one"
    assert await two.run_step("search", {"q": "x"}, lambda: call("two")) == "two"
    assert calls == ["one", "two"]

    # A new pass: identical scopes land on the same keys and replay.
    loaded = await store.load("r")
    assert loaded is not None
    again = CheckpointManager(store, loaded)
    first, second = again.scope("task", {"i": 1}), again.scope("task", {"i": 2})
    assert await second.run_step("search", {"q": "x"}, lambda: call("x")) == "two"
    assert await first.run_step("search", {"q": "x"}, lambda: call("x")) == "one"
    assert calls == ["one", "two"]


async def test_scope_identity_addresses_the_run_but_not_its_ledger() -> None:
    manager = await _manager(InMemoryCheckpointStore(), "run-9")
    scope = manager.scope("crew", {"a": 1}).scope("crew_task", {"i": 0})
    assert scope.run_id == "run-9"
    assert scope.ledger_run_id.startswith("run-9/crew:")
    assert "/crew_task:" in scope.ledger_run_id
    assert scope.checkpoint is manager.checkpoint


async def test_ambient_binding_and_durable_step_without_checkpoint() -> None:
    manager = await _manager(InMemoryCheckpointStore())
    assert current_checkpoint() is None
    with bind_checkpoint(manager):
        assert current_checkpoint() is manager
        assert resolve_checkpoint(None) is manager
        assert resolve_checkpoint("explicit") == "explicit"
    assert current_checkpoint() is None

    ran: list[int] = []

    async def fn() -> int:
        ran.append(1)
        return 7

    assert await durable_step(None, "x", {}, fn) == 7
    assert await durable_step(None, "x", {}, fn) == 7
    assert ran == [1, 1]  # no checkpoint: nothing recorded, nothing replayed
