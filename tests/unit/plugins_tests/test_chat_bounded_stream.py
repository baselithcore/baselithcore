"""``bounded_stream`` must release its upstream on every exit.

The SSE framer closes the wrapper it iterates; closing an async generator does
not close the generator *it* iterates, so without the explicit close an
abandoned stream kept the upstream LLM call running.
"""

import pytest

from plugins.api_routers.chat import bounded_stream


def _upstream(closed: list[bool]):
    async def gen():
        try:
            for i in range(100):
                yield f"chunk-{i}"
        finally:
            closed.append(True)

    return gen()


@pytest.mark.asyncio
async def test_closing_the_wrapper_closes_the_upstream():
    closed: list[bool] = []
    wrapper = bounded_stream(_upstream(closed), max_bytes=10_000, max_chunk_bytes=100)
    assert await wrapper.__anext__() == "chunk-0"
    await wrapper.aclose()
    assert closed == [True]


@pytest.mark.asyncio
async def test_truncation_closes_the_upstream():
    closed: list[bool] = []
    out = [c async for c in bounded_stream(_upstream(closed), 20, 100)]
    assert out  # something was emitted before the cap
    assert closed == [True]
