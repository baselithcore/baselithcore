"""Batches of one embed/rerank call overlap on a remote backend, in order."""

from __future__ import annotations

import asyncio

import pytest

from baselith.core.services.inference._batching import (
    REMOTE_BATCH_CONCURRENCY,
    map_batches,
)
from baselith.core.services.inference.embedding import (
    EmbeddingService,
    RemoteEmbeddingBackend,
)
from baselith.core.services.inference.rerank import RemoteRerankBackend, RerankService


class _Probe:
    """Records how many calls were in flight at once."""

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0
        self.calls = 0

    async def enter(self) -> None:
        self.calls += 1
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1


class TestMapBatches:
    async def test_results_keep_batch_order_when_later_batches_finish_first(
        self,
    ) -> None:
        async def call(delay: float) -> float:
            await asyncio.sleep(delay)
            return delay

        delays = [0.03, 0.0, 0.02, 0.01]

        assert await map_batches(delays, call, concurrency=4) == delays

    async def test_concurrency_is_bounded(self) -> None:
        probe = _Probe()

        async def call(_: int) -> None:
            await probe.enter()

        await map_batches(list(range(12)), call, concurrency=3)

        assert probe.calls == 12
        assert probe.peak == 3

    async def test_serial_backends_never_overlap(self) -> None:
        probe = _Probe()

        async def call(_: int) -> None:
            await probe.enter()

        await map_batches(list(range(5)), call, concurrency=1)

        assert probe.peak == 1

    async def test_first_failure_surfaces_as_itself_and_cancels_the_rest(
        self,
    ) -> None:
        finished: list[int] = []

        async def call(index: int) -> int:
            if index == 0:
                raise ValueError("backend down")
            await asyncio.sleep(0.05)
            finished.append(index)
            return index

        with pytest.raises(ValueError, match="backend down"):
            await map_batches(list(range(4)), call, concurrency=4)

        assert finished == []


class _EmbedBackend:
    def __init__(self, concurrency: int | None) -> None:
        self.probe = _Probe()
        if concurrency is not None:
            self.batch_concurrency = concurrency

    async def embed(self, texts: list[str], *, is_query: bool) -> list[list[float]]:
        await self.probe.enter()
        return [[float(text)] for text in texts]

    async def aclose(self) -> None:
        return None


class _ScoreBackend(_EmbedBackend):
    async def score(self, query: str, texts: list[str]) -> list[float]:
        await self.probe.enter()
        return [float(text) for text in texts]


class TestServices:
    def test_only_the_remote_backends_opt_in(self) -> None:
        assert RemoteEmbeddingBackend.batch_concurrency == REMOTE_BATCH_CONCURRENCY
        assert RemoteRerankBackend.batch_concurrency == REMOTE_BATCH_CONCURRENCY

    @pytest.mark.parametrize(("concurrency", "peak"), [(4, 4), (None, 1)])
    async def test_embedding_overlaps_batches_and_keeps_order(
        self, concurrency: int | None, peak: int
    ) -> None:
        backend = _EmbedBackend(concurrency)
        service = EmbeddingService(backend, model="m", dim=1, batch_size=2)
        texts = [str(i) for i in range(16)]

        vectors = await service.embed_documents(texts)

        assert vectors == [[float(i)] for i in range(16)]
        assert backend.probe.calls == 8
        assert backend.probe.peak == peak

    async def test_rerank_overlaps_batches_and_ranks_across_them(self) -> None:
        backend = _ScoreBackend(4)
        service = RerankService(backend, model="m", max_candidates=100, batch_size=2)

        ranked = await service.rerank("q", [str(i) for i in range(8)], top_k=3)

        assert ranked == [(7, 7.0), (6, 6.0), (5, 5.0)]
        assert backend.probe.peak == 4
