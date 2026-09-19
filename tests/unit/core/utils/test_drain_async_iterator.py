"""Tests for the async-stream-to-sync-iterator bridge.

The bridge exists because returning an async generator from a function
annotated ``Iterator[str]`` type-checks fine against ``Any`` and then fails at
the call site with ``'async_generator' object is not iterable`` — a runtime
error a long way from its cause. Each test below pins one property that makes
the bridge safe to rely on rather than merely working in the happy case.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from core.utils.concurrency import drain_async_iterator


async def _tokens(*items: str) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        for item in items:
            await asyncio.sleep(0)
            yield item

    return gen()


class TestDraining:
    def test_yields_every_item_in_order(self):
        assert list(drain_async_iterator(lambda: _tokens("a", "b", "c"))) == [
            "a",
            "b",
            "c",
        ]

    def test_empty_stream_yields_nothing(self):
        assert list(drain_async_iterator(lambda: _tokens())) == []

    def test_is_lazy(self):
        """Nothing runs until the caller asks for the first item."""
        started = []

        async def open_stream() -> AsyncIterator[int]:
            started.append("opened")

            async def gen() -> AsyncIterator[int]:
                for i in range(3):
                    yield i

            return gen()

        iterator = drain_async_iterator(open_stream)
        assert started == []
        next(iterator)
        assert started == ["opened"]
        iterator.close()


class TestErrorPropagation:
    def test_an_exception_from_the_stream_reaches_the_caller(self):
        async def open_stream() -> AsyncIterator[str]:
            async def gen() -> AsyncIterator[str]:
                yield "first"
                raise ValueError("stream blew up")

            return gen()

        iterator = drain_async_iterator(open_stream)
        assert next(iterator) == "first"
        with pytest.raises(ValueError, match="stream blew up"):
            next(iterator)

    def test_a_failure_opening_the_stream_reaches_the_caller(self):
        async def open_stream() -> AsyncIterator[str]:
            raise RuntimeError("could not open")

        iterator = drain_async_iterator(open_stream)
        with pytest.raises(RuntimeError, match="could not open"):
            next(iterator)


class TestCleanup:
    def test_abandoning_the_stream_still_runs_its_finally(self):
        """The reason this uses asyncio.Runner rather than a raw event loop.

        A caller that stops early — a client disconnecting mid-response is the
        real case — must not leave the stream's cleanup unrun, or whatever it
        held (a connection, a cursor, a budget reservation) leaks.
        """
        cleaned: list[str] = []

        async def open_stream() -> AsyncIterator[int]:
            async def gen() -> AsyncIterator[int]:
                try:
                    for i in range(1000):
                        yield i
                finally:
                    cleaned.append("closed")

            return gen()

        iterator = drain_async_iterator(open_stream)
        assert [next(iterator), next(iterator)] == [0, 1]
        iterator.close()

        assert cleaned == ["closed"]

    def test_full_consumption_also_cleans_up(self):
        cleaned: list[str] = []

        async def open_stream() -> AsyncIterator[int]:
            async def gen() -> AsyncIterator[int]:
                try:
                    yield 1
                finally:
                    cleaned.append("closed")

            return gen()

        assert list(drain_async_iterator(open_stream)) == [1]
        assert cleaned == ["closed"]


class TestEventLoopIsolation:
    def test_every_item_runs_on_one_loop(self):
        """`asyncio.run` per item cannot work: each __anext__ needs the same loop.

        A stream holding an asyncio primitive — a Queue, an Event, a Lock — binds
        it to the loop that created it, so a fresh loop per item would fail on
        the second one.
        """
        loops: list[int] = []

        async def open_stream() -> AsyncIterator[int]:
            queue: asyncio.Queue[int] = asyncio.Queue()
            for i in range(3):
                queue.put_nowait(i)

            async def gen() -> AsyncIterator[int]:
                loops.append(id(asyncio.get_running_loop()))
                for _ in range(3):
                    loops.append(id(asyncio.get_running_loop()))
                    yield await queue.get()

            return gen()

        assert list(drain_async_iterator(open_stream)) == [0, 1, 2]
        assert len(set(loops)) == 1, "items ran on more than one event loop"

    def test_leaves_no_loop_installed_behind_it(self):
        list(drain_async_iterator(lambda: _tokens("a")))
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
