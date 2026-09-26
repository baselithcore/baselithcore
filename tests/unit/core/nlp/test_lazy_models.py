"""Deferred embedder/reranker loading (core.nlp.lazy) and its chat wiring."""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.chat.dependencies import ChatDependencyConfig, create_default_dependencies
from core.nlp import LazyEmbedder, LazyReranker, aget_embedder, aget_reranker


def _raising_factory(_model: str | None) -> None:
    raise RuntimeError("sentence-transformers is not installed")


def test_default_dependencies_build_without_loading_models() -> None:
    """A plain install (no [rag] extra) must construct chat dependencies."""
    cfg = ChatDependencyConfig(
        embedder_factory=_raising_factory,
        reranker_factory=_raising_factory,
        history_enabled=False,
        response_cache_enabled=False,
        rerank_cache_enabled=False,
        precheck_cache_enabled=False,
    )
    deps = create_default_dependencies(cfg)
    assert isinstance(deps.embedder, LazyEmbedder)
    assert isinstance(deps.reranker, LazyReranker)
    assert not deps.embedder.loaded
    assert not deps.reranker.loaded


def test_default_factories_are_not_called_at_construction() -> None:
    with (
        patch("core.chat.dependencies.get_embedder") as emb,
        patch("core.chat.dependencies.get_reranker") as rer,
    ):
        create_default_dependencies(ChatDependencyConfig(history_enabled=False))
    emb.assert_not_called()
    rer.assert_not_called()


async def test_lazy_embedder_loads_off_loop_on_first_encode() -> None:
    model = MagicMock()
    model.encode = AsyncMock(return_value=[[0.1, 0.2]])
    factory_threads: list[str] = []

    def factory(name: str | None) -> MagicMock:
        factory_threads.append(threading.current_thread().name)
        assert name == "m"
        return model

    lazy = LazyEmbedder(factory, "m")
    assert not lazy.loaded
    assert await lazy.encode(["q"]) == [[0.1, 0.2]]
    assert await lazy.encode(["q2"]) == [[0.1, 0.2]]
    assert lazy.loaded
    assert len(factory_threads) == 1
    assert factory_threads[0] != threading.main_thread().name


async def test_lazy_embedder_offloads_sync_encode() -> None:
    model = MagicMock()
    model.encode.return_value = [1.0]
    lazy = LazyEmbedder(lambda _n: model, None)
    assert await lazy.encode("x") == [1.0]
    model.encode.assert_called_once_with("x")


def test_lazy_reranker_loads_on_predict_only() -> None:
    model = MagicMock()
    model.predict.return_value = [0.9]
    calls: list[str | None] = []

    def factory(name: str | None) -> MagicMock:
        calls.append(name)
        return model

    lazy = LazyReranker(factory, "r")
    assert calls == []
    assert lazy.predict([("q", "d")]) == [0.9]
    assert calls == ["r"]


def test_lazy_private_attribute_does_not_load() -> None:
    lazy = LazyReranker(_raising_factory, None)
    with pytest.raises(AttributeError):
        _ = lazy._missing
    assert not lazy.loaded


async def test_lazy_embedder_surfaces_missing_extra_on_use() -> None:
    lazy = LazyEmbedder(_raising_factory, None)
    with pytest.raises(RuntimeError, match="sentence-transformers"):
        await lazy.encode("x")


async def test_async_accessors_run_factory_in_worker_thread() -> None:
    seen: list[str] = []

    def fake(name: str | None = None) -> str:
        seen.append(threading.current_thread().name)
        return f"model:{name}"

    with (
        patch("core.nlp.models.get_embedder", side_effect=fake),
        patch("core.nlp.models.get_reranker", side_effect=fake),
    ):
        assert await aget_embedder("e") == "model:e"
        assert await aget_reranker("r") == "model:r"
    assert all(name != threading.main_thread().name for name in seen)
