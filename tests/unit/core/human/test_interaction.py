"""Human-in-the-loop interaction manager.

Covers the two defects this module used to carry:

* a **sync** callback ran inline on the event loop and ignored
  ``timeout_seconds`` entirely — a blocking UI/CLI prompt froze every other
  task in the process for as long as the human took to answer;
* the pending registry *deleted* a request the moment it reached a terminal
  state, so nothing could ever read back the outcome of a request.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.human.interaction import (
    DEFAULT_MAX_RECENT_REQUESTS,
    HumanIntervention,
    HumanRequest,
    InteractionStatus,
    InteractionType,
)


class TestSyncCallbackOffLoop:
    async def test_blocking_callback_does_not_block_the_loop(self) -> None:
        """A blocking sync callback must run on a worker thread."""
        started = threading.Event()

        def blocking(request: HumanRequest) -> str:
            started.set()
            time.sleep(0.2)
            return "answered"

        manager = HumanIntervention(callback=blocking)
        ticks = 0

        async def tick() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        try:
            answer = await manager.ask_input("name?")
        finally:
            ticker.cancel()
        assert answer == "answered"
        assert started.is_set()
        # The loop kept running while the callback blocked its thread.
        assert ticks >= 5

    async def test_sync_callback_runs_off_the_main_thread(self) -> None:
        seen: list[int] = []

        def record(request: HumanRequest) -> str:
            seen.append(threading.get_ident())
            return "ok"

        manager = HumanIntervention(callback=record)
        await manager.ask_input("q")
        assert seen and seen[0] != threading.get_ident()

    async def test_sync_callback_timeout_is_honoured(self) -> None:
        """``timeout`` used to be ignored entirely for sync callbacks."""

        def slow(request: HumanRequest) -> bool:
            # 2s, not 5s: a timed-out sync callback's thread cannot be
            # cancelled, so a long sleep here outlives the test and stalls
            # interpreter exit. The < 3s assertion below still proves the
            # 1s timeout fired rather than the callback finishing.
            time.sleep(2)
            return True

        manager = HumanIntervention(callback=slow)
        start = time.perf_counter()
        approved = await manager.request_approval("deploy?", timeout=1)
        elapsed = time.perf_counter() - start
        assert approved is False
        assert elapsed < 3
        recent = manager.get_recent_requests()
        assert recent[-1].status is InteractionStatus.TIMEOUT

    async def test_async_callback_timeout_still_honoured(self) -> None:
        async def slow(request: HumanRequest) -> bool:
            await asyncio.sleep(5)
            return True

        manager = HumanIntervention(callback=slow)
        assert await manager.request_approval("deploy?", timeout=1) is False

    async def test_sync_callable_returning_awaitable_is_awaited(self) -> None:
        """A callable object whose ``__call__`` is async is still supported."""

        class AsyncCallable:
            async def __call__(self, request: HumanRequest) -> str:
                await asyncio.sleep(0)
                return "from-async-callable"

        manager = HumanIntervention(callback=AsyncCallable())
        assert await manager.ask_input("q") == "from-async-callable"


class TestTerminalRequestRegistry:
    async def test_completed_request_is_retained(self) -> None:
        manager = HumanIntervention(callback=lambda request: True)
        await manager.request_approval("ship it?")
        recent = manager.get_recent_requests()
        assert len(recent) == 1
        assert recent[0].status is InteractionStatus.COMPLETED
        assert recent[0].response is True
        # Pending registry is for in-flight work only.
        assert manager.get_pending_requests() == []
        assert manager.has_pending_requests() is False

    async def test_denied_request_retains_the_decision(self) -> None:
        """A human who answered "no" is COMPLETED; the ``no`` is the response.

        REJECTED is reserved for requests no human ever saw.
        """

        def deny(request: HumanRequest) -> bool:
            return False

        manager = HumanIntervention(callback=deny)
        assert await manager.request_approval("rm -rf?") is False
        retained = manager.get_recent_requests()[0]
        assert retained.status is InteractionStatus.COMPLETED
        assert retained.response is False

    async def test_no_callback_records_rejection(self) -> None:
        manager = HumanIntervention()
        assert await manager.request_approval("anything?") is False
        assert manager.get_recent_requests()[0].status is InteractionStatus.REJECTED

    async def test_callback_error_records_rejection(self) -> None:
        def boom(request: HumanRequest) -> bool:
            raise RuntimeError("ui exploded")

        manager = HumanIntervention(callback=boom)
        assert await manager.request_approval("go?") is False
        assert manager.get_recent_requests()[0].status is InteractionStatus.REJECTED

    async def test_recent_registry_is_bounded_and_ordered(self) -> None:
        manager = HumanIntervention(callback=lambda request: "ok", max_recent=4)
        for index in range(10):
            await manager.ask_input(f"q{index}")
        recent = manager.get_recent_requests()
        assert len(recent) == 4
        # Oldest evicted; order is oldest-first.
        assert [r.description for r in recent] == ["q6", "q7", "q8", "q9"]

    async def test_default_bound_is_256(self) -> None:
        assert DEFAULT_MAX_RECENT_REQUESTS == 256
        manager = HumanIntervention(callback=lambda request: "ok")
        assert manager._max_recent == DEFAULT_MAX_RECENT_REQUESTS

    async def test_get_recent_requests_limit_and_status_filter(self) -> None:
        manager = HumanIntervention(callback=lambda request: "ok")
        await manager.ask_input("a")
        await manager.ask_input("b")
        await manager.notify("c")
        assert [r.description for r in manager.get_recent_requests(limit=2)] == [
            "b",
            "c",
        ]
        completed = manager.get_recent_requests(status=InteractionStatus.COMPLETED)
        assert {r.description for r in completed} == {"a", "b", "c"}
        assert manager.get_recent_requests(status=InteractionStatus.TIMEOUT) == []

    async def test_get_request_finds_pending_and_terminal(self) -> None:
        seen: dict[str, HumanRequest] = {}

        def capture(request: HumanRequest) -> str:
            seen["request"] = request
            # Visible in the pending registry while it is in flight.
            return "ok"

        manager = HumanIntervention(callback=capture)
        await manager.ask_input("q")
        request = seen["request"]
        assert manager.get_request(request.id) is request
        assert manager.get_request(request.id).status is InteractionStatus.COMPLETED

    async def test_unknown_request_id_returns_none(self) -> None:
        from uuid import uuid4

        manager = HumanIntervention()
        assert manager.get_request(uuid4()) is None

    async def test_pending_visible_while_in_flight(self) -> None:
        release = asyncio.Event()

        async def slow(request: HumanRequest) -> str:
            await release.wait()
            return "done"

        manager = HumanIntervention(callback=slow)
        task = asyncio.create_task(manager.ask_input("waiting?"))
        await asyncio.sleep(0.01)
        assert manager.has_pending_requests() is True
        assert manager.get_pending_requests()[0].description == "waiting?"
        release.set()
        assert await task == "done"
        assert manager.get_pending_requests() == []
        assert len(manager.get_recent_requests()) == 1


class TestApprovalStatus:
    async def test_selection_records_completed(self) -> None:
        manager = HumanIntervention(callback=lambda request: "staging")
        assert (
            await manager.request_selection("where?", ["staging", "prod"]) == "staging"
        )
        assert manager.get_recent_requests()[0].type is InteractionType.SELECTION

    async def test_notify_completes_without_response(self) -> None:
        calls: list[HumanRequest] = []

        async def sink(request: HumanRequest) -> None:
            calls.append(request)

        manager = HumanIntervention(callback=sink)
        await manager.notify("done", context={"task": "t1"})
        assert calls[0].type is InteractionType.NOTIFICATION
        assert manager.get_recent_requests()[0].status is InteractionStatus.COMPLETED


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])


class TestDedicatedExecutor:
    """Sync callbacks must not run on the interpreter's default executor.

    A human approval blocks for as long as the human takes, and a timed-out
    one never returns its thread at all. The default executor is shared with
    every other ``to_thread`` caller in the framework (SSRF DNS resolution,
    audit-log appends, tokenization) and is only ``cpu_count + 4`` wide, so a
    handful of pending approvals would starve them — the same argument
    ``core.utils.concurrency.get_inference_executor`` makes for model calls.
    """

    async def test_callback_runs_on_the_hitl_pool(self) -> None:
        names: list[str] = []

        def record(request: HumanRequest) -> str:
            names.append(threading.current_thread().name)
            return "ok"

        manager = HumanIntervention(callback=record)
        await manager.ask_input("q")
        assert names and names[0].startswith("baselith-hitl")

    async def test_default_executor_is_not_used(self, monkeypatch) -> None:
        """``asyncio.to_thread`` is the default executor — it must not be hit."""

        def _boom(*args, **kwargs):
            raise AssertionError("HITL callback used the default executor")

        monkeypatch.setattr(asyncio, "to_thread", _boom)
        manager = HumanIntervention(callback=lambda request: "ok")
        assert await manager.ask_input("q") == "ok"

    def test_pool_is_a_singleton_sized_from_settings(self) -> None:
        from core.human.executor import (
            DEFAULT_HITL_CALLBACK_THREADS,
            get_hitl_executor,
            shutdown_hitl_executor,
        )

        assert DEFAULT_HITL_CALLBACK_THREADS == 8
        shutdown_hitl_executor()
        try:
            pool = get_hitl_executor()
            assert get_hitl_executor() is pool
            assert pool._max_workers == 8
        finally:
            shutdown_hitl_executor()

    def test_pool_size_comes_from_the_setting(self, monkeypatch) -> None:
        import core.human.executor as executor_mod

        monkeypatch.setattr(executor_mod, "_configured_threads", lambda: 3)
        executor_mod.shutdown_hitl_executor()
        try:
            assert executor_mod.get_hitl_executor()._max_workers == 3
        finally:
            executor_mod.shutdown_hitl_executor()

    def test_shutdown_is_idempotent(self) -> None:
        from core.human.executor import get_hitl_executor, shutdown_hitl_executor

        get_hitl_executor()
        shutdown_hitl_executor()
        shutdown_hitl_executor()

    def test_orchestration_config_exposes_the_thread_count(self) -> None:
        from core.config.orchestration import OrchestrationConfig

        assert OrchestrationConfig().hitl_callback_threads == 8


class TestCancellation:
    async def test_cancelled_request_is_retired_with_a_terminal_status(self) -> None:
        """A cancelled request used to land in the archive still PENDING."""
        started = asyncio.Event()

        async def never(request: HumanRequest) -> str:
            started.set()
            await asyncio.Event().wait()
            return "never"

        manager = HumanIntervention(callback=never)
        task = asyncio.create_task(manager.ask_input("waiting?"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert manager.get_pending_requests() == []
        retained = manager.get_recent_requests()
        assert len(retained) == 1
        assert retained[0].status is InteractionStatus.CANCELLED
        assert retained[0].status is not InteractionStatus.PENDING

    async def test_cancelled_status_is_filterable(self) -> None:
        async def never(request: HumanRequest) -> str:
            await asyncio.Event().wait()
            return "never"

        manager = HumanIntervention(callback=never)
        task = asyncio.create_task(manager.ask_input("q"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert len(manager.get_recent_requests(status=InteractionStatus.CANCELLED)) == 1


class TestPublicExports:
    def test_package_exports_the_bound(self) -> None:
        import core.human as human

        assert human.DEFAULT_MAX_RECENT_REQUESTS == 256
        assert "DEFAULT_MAX_RECENT_REQUESTS" in human.__all__
