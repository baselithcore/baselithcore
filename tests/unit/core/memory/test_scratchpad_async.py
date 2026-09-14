"""Async scratchpad surface.

The backend protocol was synchronous, so every scratchpad read/write from the
agent loop ran a blocking call on the event loop — with the Redis backend that
is a network round-trip per section, stalling every other task in the worker.
The protocol now carries ``aget``/``aset``/``adelete``/``alist``/``aclear``;
the sync methods stay as thin wrappers for non-async callers, and a legacy
sync-only backend is still accepted (its calls are offloaded to a thread).
"""

from __future__ import annotations

import asyncio

import pytest

from core.memory.scratchpad import (
    InMemoryScratchpadBackend,
    Scratchpad,
    ScratchpadOverflowError,
)


def _pad() -> Scratchpad:
    return Scratchpad(backend=InMemoryScratchpadBackend())


class TestAsyncFacade:
    async def test_async_roundtrip(self) -> None:
        pad = _pad()
        await pad.aupdate_section("t1", "goal", "find primes")
        assert await pad.aread_section("t1", "goal") == "find primes"

    async def test_async_and_sync_share_storage(self) -> None:
        pad = _pad()
        pad.update_section("t1", "plan", "step 1")
        assert await pad.aread_section("t1", "plan") == "step 1"
        await pad.aupdate_section("t1", "plan", "step 2")
        assert pad.read_section("t1", "plan") == "step 2"

    async def test_async_list_and_clear_section(self) -> None:
        pad = _pad()
        await pad.aupdate_section("t1", "b", "2")
        await pad.aupdate_section("t1", "a", "1")
        assert await pad.alist_sections("t1") == ["a", "b"]
        await pad.aclear_section("t1", "a")
        assert await pad.alist_sections("t1") == ["b"]

    async def test_async_clear_thread(self) -> None:
        pad = _pad()
        await pad.aupdate_section("t1", "a", "1")
        await pad.aclear("t1")
        assert await pad.alist_sections("t1") == []

    async def test_async_read_all(self) -> None:
        pad = _pad()
        await pad.aupdate_section("t1", "goal", "ship")
        await pad.aupdate_section("t1", "plan", "design")
        text = await pad.aread_all("t1")
        assert "## goal" in text and "ship" in text
        assert "## plan" in text and "design" in text

    async def test_async_read_all_empty(self) -> None:
        assert await _pad().aread_all("nobody") == ""

    async def test_async_enforces_the_same_caps(self) -> None:
        pad = Scratchpad(
            backend=InMemoryScratchpadBackend(),
            max_section_bytes=16,
            max_sections=2,
        )
        with pytest.raises(ScratchpadOverflowError):
            await pad.aupdate_section("t1", "big", "x" * 17)
        await pad.aupdate_section("t1", "a", "1")
        await pad.aupdate_section("t1", "b", "2")
        with pytest.raises(ScratchpadOverflowError):
            await pad.aupdate_section("t1", "c", "3")

    async def test_async_rejects_empty_section_name(self) -> None:
        with pytest.raises(ValueError):
            await _pad().aupdate_section("t1", "", "x")

    async def test_threads_stay_isolated_under_async(self) -> None:
        pad = _pad()
        await asyncio.gather(
            pad.aupdate_section("alice", "secret", "A"),
            pad.aupdate_section("bob", "secret", "B"),
        )
        assert await pad.aread_section("alice", "secret") == "A"
        assert await pad.aread_section("bob", "secret") == "B"


class TestInMemoryBackendAsync:
    async def test_backend_async_methods(self) -> None:
        backend = InMemoryScratchpadBackend()
        await backend.aset("t1", "k", "v")
        assert await backend.aget("t1", "k") == "v"
        assert await backend.alist("t1") == ["k"]
        assert await backend.aget_all("t1") == {"k": "v"}
        await backend.adelete("t1", "k")
        assert await backend.aget("t1", "k") is None
        await backend.aset("t1", "k", "v")
        await backend.aclear("t1")
        assert await backend.alist("t1") == []


class _LegacySyncBackend:
    """A pre-async backend: only the synchronous protocol methods."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self.calls: list[str] = []

    def get(self, thread_id: str, section: str) -> str | None:
        self.calls.append("get")
        return self.store.get(thread_id, {}).get(section)

    def set(self, thread_id: str, section: str, content: str) -> None:
        self.calls.append("set")
        self.store.setdefault(thread_id, {})[section] = content

    def delete(self, thread_id: str, section: str) -> None:
        self.calls.append("delete")
        self.store.get(thread_id, {}).pop(section, None)

    def list_sections(self, thread_id: str) -> list[str]:
        self.calls.append("list_sections")
        return sorted(self.store.get(thread_id, {}))

    def clear(self, thread_id: str) -> None:
        self.calls.append("clear")
        self.store.pop(thread_id, None)


class TestLegacyBackendCompatibility:
    async def test_sync_only_backend_still_works_through_the_async_facade(
        self,
    ) -> None:
        backend = _LegacySyncBackend()
        pad = Scratchpad(backend=backend)
        await pad.aupdate_section("t1", "a", "1")
        assert await pad.aread_section("t1", "a") == "1"
        assert await pad.alist_sections("t1") == ["a"]
        assert "## a" in await pad.aread_all("t1")
        await pad.aclear_section("t1", "a")
        await pad.aclear("t1")
        assert "set" in backend.calls and "get" in backend.calls

    async def test_legacy_backend_calls_run_off_the_loop(self) -> None:
        """A blocking legacy backend must not stall the event loop."""
        import threading
        import time

        main_thread = threading.get_ident()
        seen: list[int] = []

        class _Blocking(_LegacySyncBackend):
            def set(self, thread_id: str, section: str, content: str) -> None:
                seen.append(threading.get_ident())
                time.sleep(0.05)
                super().set(thread_id, section, content)

        pad = Scratchpad(backend=_Blocking())
        await pad.aupdate_section("t1", "a", "1")
        assert seen and seen[0] != main_thread


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
